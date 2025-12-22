#!/bin/bash
# Cloud-init bootstrap for Minecraft server restore + start with CloudWatch player reporting

sudo -u ubuntu bash <<'EOF'
LOGFILE="/home/ubuntu/bootstrap.log"
# ---Your S3 Bucket here---
BUCKET="greysminecraftserver"
MC_DIR="/home/ubuntu/minecraft"

echo "[BOOT] $(date) Bootstrapping Minecraft server" >> $LOGFILE

# --- Install dependencies ---
sudo apt-get update -y && sudo apt-get install -y awscli tmux openjdk-21-jre-headless net-tools iproute2

# --- Restore world data from S3 ---
echo "[BOOT] Restoring all worlds from S3..." >> $LOGFILE
mkdir -p "$MC_DIR/world" "$MC_DIR/world_nether" "$MC_DIR/world_the_end"

aws s3 sync "s3://$BUCKET/world/" "$MC_DIR/world/" --delete >> $LOGFILE 2>&1
aws s3 sync "s3://$BUCKET/world_nether/" "$MC_DIR/world_nether/" --delete >> $LOGFILE 2>&1
aws s3 sync "s3://$BUCKET/world_the_end/" "$MC_DIR/world_the_end/" --delete >> $LOGFILE 2>&1

# --- Start Minecraft server inside tmux ---
cd "$MC_DIR"
if ! tmux has-session -t mc 2>/dev/null; then
  echo "[BOOT] Starting Minecraft server in tmux..." >> $LOGFILE
  tmux new-session -d -s mc "java -Xms5G -Xmx5G -XX:+UseG1GC -XX:+ParallelRefProcEnabled -XX:MaxGCPauseMillis=50 \
-XX:+UnlockExperimentalVMOptions -XX:+DisableExplicitGC -XX:+AlwaysPreTouch \
-XX:G1NewSizePercent=30 -XX:G1MaxNewSizePercent=40 -XX:G1HeapRegionSize=8M \
-XX:G1ReservePercent=20 -XX:G1HeapWastePercent=5 -XX:G1MixedGCCountTarget=4 \
-XX:InitiatingHeapOccupancyPercent=15 -XX:G1MixedGCLiveThresholdPercent=90 \
-XX:G1RSetUpdatingPauseTimePercent=5 -XX:SurvivorRatio=32 -XX:+PerfDisableSharedMem \
-XX:MaxTenuringThreshold=1 -Dusing.aikars.flags=https://mcflags.emc.gs \
-Daikars.new.flags=true -jar server.jar nogui"
fi

# --- CloudWatch ActivePlayers metric reporting ---
aws configure set region us-east-1

#--- Pre-seed Cloudwatch with fake "1 player" value to prevent false startup alarm---
aws cloudwatch put-metric-data \
  --namespace "MinecraftServer" \
  --metric-name "ActivePlayers" \
  --value 1 \
  --unit Count \
  --region us-east-1
echo "[BOOT] Seeded CloudWatch metric with initial datapoint." >> $LOGFILE

cat <<'SCRIPT' > /home/ubuntu/report_players.sh
#!/bin/bash
REGION="us-east-1"
NAMESPACE="MinecraftServer"
METRIC_NAME="ActivePlayers"
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)

PLAYERS=$(ss -H state established sport = :25565 | wc -l)
if [ -z "$PLAYERS" ] || [ "$PLAYERS" -lt 0 ]; then
  PLAYERS=$(sudo netstat -an | grep ESTABLISHED | grep ":25565" | wc -l)
fi

aws cloudwatch put-metric-data \
  --namespace "$NAMESPACE" \
  --metric-name "$METRIC_NAME" \
  --dimensions InstanceId=shared \
  --value "$PLAYERS" \
  --unit Count \
  --region "$REGION"

echo "$(date): Reported $PLAYERS active players" >> /home/ubuntu/report.log
SCRIPT

chmod +x /home/ubuntu/report_players.sh
(crontab -l 2>/dev/null; echo "* * * * * /home/ubuntu/report_players.sh >> /home/ubuntu/report.log 2>&1") | crontab -

echo "[BOOT] CloudWatch reporting configured." >> $LOGFILE
echo "[BOOT] Startup complete." >> $LOGFILE
EOF
