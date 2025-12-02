
AWS Stateless Minecraft Server
-------------------------------

This project has been my introduction to automation basics and cloud hosting services. Using the foundational knowledge I gained while studying for my AWS solutions architect certification, I set out to build the cheapest possible Minecraft server, with minimal overhead and no manual upkeep. This system can spin up only when needed and stay off when idle. 

> **Note on CloudFormation:**  
> I intentionally do **not** provide a one-click CloudFormation deployment link in this repo.  
> Blindly launching infrastructure templates from GitHub into your own AWS account is generally
> not considered best practice. You should always review the code, understand the IAM
> permissions, and adapt the templates to your own security and cost requirements before
> deploying.
>
> I will document the setup process using pictures and detailed instructions for anyone who would like to implement this cheap automated minecraft server.
> 
----------------------------------------
**Architectural Diagram:**

Reference-style: 
![alt text][logo]

[logo]: https://github.com/greypaschall/autoServerProject/blob/main/Diagrams%20us-east-1-1.png "Logo Title Text 2"


----------------------------------------


**Flow and detailed explanation:**

* **mc_proxy.py** is a port listener and conditional proxy. It will run in a tmux session on a nano EC2 instance for minimum costs.

  * The Minecraft client connects to port 25565 on the nano EC2 instance.

  * Before anything happens, the script looks at the TCP packet recieved from your Minecraft client. (We will call this the handshake)
   
   * Minecraft Handshake structure:
      * packet length
      * an ID for the packet (Here it will be 0x00)
      * Protocol Version
      * Target ip and port
      * Next State (0x01 for status request and 0x02 for login attempt)
      * Additional data if 0x02
   
   * There are two conditions:
     * If the handshake's next state is 0x01 this indicates a simple status ping. -> (Result: Ignore Handshake)
       * (Context): Every IPv4 address is being constantly scanned by bots checking for a response. Most of the time they are just pinging the ip which results in a next state of 0x01.
       * Without checking the next state, simple status pings can cause the server to startup when it is not supposed to 100+ times a day.

     * If the handshake's next state is 0x02 this indicates an actual login request from the Minecraft client. -> (Result: Invoke Server Startup)
       * (Context): In the Minecraft TCP sequence, a login packet with a next state of 0x02 will have a username and user ID associated with it.
       * Only a real player initiating a connection to the server will be able to invoke the startup.
   
   * When a genuine login attempt is recieved:
     * Script checks for a tagged EC2 instance (MinecraftServer = True) to see if it is already running.

     * If no instance is found running:
       * The script invokes the 'StartMinecraftServer' on AWS lambda using boto3, this will start the server with an instance tag (MinecraftServer = True)
       * Enforces a cooldown period of 180 seconds to prevent multiple invocations during startup
       * Upon succesful startup, the proxy can now see the insance online via its tag and start tunneling connections over to that server
       * (Player connects to the IP of the nano tier EC2 hosting the proxy script -> Connection Times out -> ~ 2 minutes later player connects to same IP again -> This time the player connection is tunneled over to large tier EC2 hosting the Minecraft server)
      
      * If an instance is already running or has just entered a running state:
        * The Proxy will switch into its "Forwarding mode." Any connections are tunneled over to the Minecraft Server.
        * The player does not see this. The server IP they use to join will remain the same for as long as the mc_proxy EC2 instance is running.

* **StartMinecraftServer AWS Lambda** Lambda is an event driven compute service. When it is invoked from mc_proxy.py, it runs it's own python script within AWS to launch the Minecraft Server instance using a bootstrapped launch template, tagging the instance with (MinecraftServer = True)

  * This script in Lambda will check the instance tags and do nothing if the server is already running
  * If the server is not running, it will launch it from a template containing a bootstrap script, and tag it

    * **The launch template:**
      * A prebaked Ubuntu AMI containing Server.jar and correlating config files and a world data location
        * The AMI will also contain `mcsave.sh`, a shell script to be called during the shut-down phase  
      * t3.large instance for running the server (This can vary depending on performance needs and a medium instance works well too)
      * 8 GiB EBS gp3 volume with 3000 IOPS
      * user data (where we put the bootstrap script)

    * **Inside bootstrap.sh:**
      *  Installs dependencies (awscli, tmux, Java OpenJDK, iproute2, net-tools(fallback may not be needed))
      *  Syncs data from S3 bucket to the in-use EBS volume
      *  Starts the Minecraft server in a tmux session
      * Creates a shared CloudWatch metric called ActivePlayers  
        (The script preseeds the metric with a temporary value of 1 to prevent alarms while starting up)
      * Creates a small Shell script that:
        * Uses iproute2 (`ss`) to track active players  
        * Uses net-tools (`netstat`) as a fallback if `ss` fails  
        * Pushes the metric data to CloudWatch
      * Runs the shell script through crontab every single minute for active player updates

   
   * Now back to the **StartMinecraftServer** Lambda will see the tagged instance running
   * It will create a CloudWatch alarm tied to the shared **ActivePlayers** metric
     * The alarm name is formatted as `AutoShutdown-<instance-id>` (Example: `AutoShutdown-i-0123456789abcdef0`) — Though this isnt used to share the actual instance id with Lambda, it is used to delete the alarm later on.
     * The metric uses a shared dimension: `InstanceId=shared`, so the alarm is based on `MinecraftServer / ActivePlayers / InstanceId=shared`
     * If the player count falls below 0 long enough for it to be updated in CloudWatch metrics (3 minutes) the alarm will trigger.
     * When this happens, CloudWatch sends an event to an SNS topic, which the **SaveWorldShutdown** Lambda is subscribed to.
    
* **SaveWorldShutdown AWS Lambda** This one handles autoshutdown and saving world files back to the S3 bucket. It also deletes the CloudWatch alarm.

  *  The Lambda receives the alarm event from SNS and inspects the CloudWatch `Trigger.Dimensions`
  *  There it finds `InstanceId=shared` — this is expected, because the metric is intentionally published with a shared dimension rather than the real EC2 instance-id. (*The whole reason for using a shared metric is to prevent a new metric from being created on each start-up.*)
  * Because `InstanceId` is `"shared"`, the Lambda resolves the **actual** Minecraft server InstanceId by:
    * Calling `DescribeInstances` with filters:
      * `tag:MinecraftServer = True`
      * `instance-state-name = running or pending`
    * It takes the first matching instance and treats **that** `InstanceId` as the active server
  * The resolved instance-id is then double-checked confimred with the same tag as an extra safety measure
  * After the instance selected for shutdown is confirmed to be the correct one:
    * The Lambda uses SSM to run `mcsave.sh` on the instance **before** termination so worlds are safely saved and synced.
    * It waits for the SSM command to complete or timeout.
    * It sends a terminate command for that EC2 instance.
    * Finally, it deletes the corresponding CloudWatch alarm using its name (ex. `AutoShutdown-<instance-id>`).
  



  

Costs:
___________

*Idle Costs:

-t4g.nano EC2 instance ~ $3.10/month (This small Ubuntu server runs the TCP listener in a tmux session 24/7)
  - associated EBS volume ~ $0.80/month (gp2 volume with 8gb stores Python listener script)

*Active Costs (You are only charged when a player is using the server):

-t3.large EC2 instance ~ $0.0832/hour 
  - associated EBS volume ~ $0.00088/hour (temporary volume provisioned at startup and deleted at shutdown)


*Free Services:

-S3

-VPC w/ S3 Gateway

*Free in the context of this architecture:

-CloudWatch

-SNS

-StartMinecraftServer AWS Lambda ~  Free invocations under 1 million rquests

-StopMinecraftServer AWS Lambda ~ Free invocations under 1 million rquests





