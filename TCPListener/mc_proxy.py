import socket
import threading
import time
import boto3
import json

# --- Config ---
LAMBDA_FUNCTION = "StartMinecraftServer"
REGION = "us-east-1"
MINECRAFT_PORT = 25565
BUFFER_SIZE = 4096
INSTANCE_TAG_KEY = "MinecraftServer"
INSTANCE_TAG_VALUE = "True"
COOLDOWN = 180  # seconds

invoke_lock = threading.Lock()
last_invoked = 0

ec2 = boto3.client("ec2", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)

# ---------- AWS helpers ----------
def get_running_instance_ip():
    """Return public IP of a RUNNING instance tagged MinecraftServer=True, else None."""
    resp = ec2.describe_instances(
        Filters=[
            {"Name": f"tag:{INSTANCE_TAG_KEY}", "Values": [INSTANCE_TAG_VALUE]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )
    for r in resp.get("Reservations", []):
        for inst in r.get("Instances", []):
            ip = inst.get("PublicIpAddress")
            if ip:
                return ip
    return None


def port_open(host, port, timeout=2):
    """True if TCP port is open."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


# ---------- VarInt utils ----------
def read_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def read_varint(sock):
    num = 0
    shift = 0
    while True:
        b = read_exact(sock, 1)[0]
        num |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
        if shift > 35:
            raise ValueError("VarInt too big")
    return num


def read_varint_from_buf(buf, idx):
    num = 0
    shift = 0
    while True:
        if idx >= len(buf):
            raise ValueError("VarInt buffer overrun")
        b = buf[idx]
        idx += 1
        num |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
        if shift > 35:
            raise ValueError("VarInt too big")
    return num, idx


def write_varint(n):
    out = bytearray()
    while True:
        temp = n & 0x7F
        n >>= 7
        if n != 0:
            temp |= 0x80
        out.append(temp)
        if n == 0:
            break
    return bytes(out)


# ---------- Forwarding ----------
def pipe(src, dst):
    try:
        while True:
            data = src.recv(BUFFER_SIZE)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        try:
            src.close()
        except:
            pass
        try:
            dst.close()
        except:
            pass


# ---------- Per-connection handler ----------
def handle_client(client_socket, client_address):
    global last_invoked
    print(f"[JOIN] {client_address} connected")

    # 1) If MC is already running, just tunnel raw traffic
    ip = get_running_instance_ip()
    if ip and port_open(ip, MINECRAFT_PORT):
        try:
            print(f"[FORWARD] Connecting to {ip}:{MINECRAFT_PORT}")
            real = socket.create_connection((ip, MINECRAFT_PORT), timeout=10)
        except Exception as e:
            print(f"[ERROR] connect failed: {e}")
            client_socket.close()
            return
        print("[TUNNEL] Forwarding traffic.")
        threading.Thread(target=pipe, args=(client_socket, real), daemon=True).start()
        threading.Thread(target=pipe, args=(real, client_socket), daemon=True).start()
        return

    # 2) Server is OFFLINE here → read handshake once and classify it
    try:
        frame_len = read_varint(client_socket)
        handshake = read_exact(client_socket, frame_len)
    except Exception as e:
        print(f"[ERROR] reading handshake: {e}")
        client_socket.close()
        return

    # Parse handshake buffer to get next_state (1=status, 2=login)
    try:
        buf = handshake
        idx = 0
        packet_id, idx = read_varint_from_buf(buf, idx)
        if packet_id != 0x00:
            print("[WARN] Not a handshake packet, closing.")
            client_socket.close()
            return

        protocol_version, idx = read_varint_from_buf(buf, idx)
        addr_len, idx = read_varint_from_buf(buf, idx)
        idx += addr_len        # skip server address
        idx += 2               # skip port
        next_state, idx = read_varint_from_buf(buf, idx)
    except Exception as e:
        print(f"[ERROR] parsing handshake: {e}")
        client_socket.close()
        return

    # 3) If it's a pure STATUS ping (server list / query sites) → do NOT start Lambda
    if next_state == 1:
        print("[STATUS] Pure status ping. Returning MOTD only, not starting server.")

        try:
            # Status Request packet (id=0x00, empty body)
            _len2 = read_varint(client_socket)
            pkt_id2 = read_varint(client_socket)
            if pkt_id2 != 0x00:
                print(f"[WARN] Unexpected status packet id {pkt_id2}")
                client_socket.close()
                return

            motd_text = "§eServer is OFFLINE. It spins up only when you actually join. Estimated Spinup Time: 1 minute. Please do not idle on the server ping menu."
            payload = {
                "version": {"name": "1.21.1", "protocol": protocol_version},
                "players": {"max": 20, "online": 0},
                "description": {"text": motd_text},
            }
            json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            body = write_varint(0x00) + write_varint(len(json_bytes)) + json_bytes
            client_socket.sendall(write_varint(len(body)) + body)

            # Optional Ping (id=0x01)
            try:
                _len3 = read_varint(client_socket)
                pkt_id3 = read_varint(client_socket)
                if pkt_id3 == 0x01:
                    remaining = read_exact(client_socket, _len3 - 1)  # 8-byte payload
                    pong = write_varint(0x01) + remaining
                    client_socket.sendall(write_varint(len(pong)) + pong)
            except Exception:
                # If client doesn’t send ping, that’s fine
                pass
        finally:
            try:
                client_socket.close()
            except:
                pass
        return

    # 4) If it's a LOGIN attempt → this is where we actually start the MC server
    if next_state == 2:
        with invoke_lock:
            now = time.time()
            if now - last_invoked > COOLDOWN:
                print("[LAMBDA] Invoking StartMinecraftServer due to login attempt…")
                try:
                    lambda_client.invoke(
                        FunctionName=LAMBDA_FUNCTION,
                        InvocationType="Event",
                    )
                    last_invoked = now
                except Exception as e:
                    print(f"[ERROR] lambda invoke failed: {e}")
            else:
                print("[LAMBDA] Skipped, still in cooldown window.")

        # Tell the client what’s happening using a LOGIN disconnect packet
        try:
            msg = {"text": "Server is spinning up. Please try again in ~30 seconds."}
            msg_bytes = json.dumps(msg, ensure_ascii=False).encode("utf-8")
            body = write_varint(0x00) + write_varint(len(msg_bytes)) + msg_bytes
            client_socket.sendall(write_varint(len(body)) + body)
        except Exception as e:
            print(f"[ERROR] sending disconnect: {e}")
        finally:
            try:
                client_socket.close()
            except:
                pass
        return

    # 5) Anything else → ignore
    print(f"[WARN] Unknown handshake next_state={next_state}. Closing.")
    client_socket.close()


# ---------- Listener ----------
def start_proxy():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", MINECRAFT_PORT))
    s.listen(64)
    print(f"[LISTENING] Proxy on {MINECRAFT_PORT}")
    try:
        while True:
            c, addr = s.accept()
            threading.Thread(target=handle_client, args=(c, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\n[EXIT] Proxy shutting down…")
    finally:
        s.close()


if __name__ == "__main__":
    start_proxy()
