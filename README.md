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
> I will document the setup process using pictures an detailed instructions for anyone who would like to implement this cheap automated minecraft server.
> 
----------------------------------------

Flow and detailed explanation:

* mc_proxy.py is a port listener and conditional proxy. It will run in a tmux session on a nano EC2 instance for minimum costs.

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
       * Only a real player initiating a connection to the server will be able to invoke the startup

The script inspects the Minecraft handshake packet to determine what the client is doing. - Ingame they will see a motd saying "Starting up please wait ~2 minutes"

-The launch template bootstraps the instance:
  - restoring world data from an S3 bucket
  - start the minecraft server with a custom launch configuration in a tmux session
  - creates a cloudwatch metric if one does not exist to publish active players report to AWS
  - dynamically creates a script to report active players connected
  - runs reportplayers script in a crontab every minute
  
-Player reconnects to listener IP and is tunneled to the IP of the spun up instance

-After all players have disconnected, wait for metric update

-After ~4 minutes the metric has reported 0 active players and the Alarm goes off

-Alarm triggers SNS to call the lambda function to save the world back to S3, terminate the server instance, and delete the cloudwatch alarm
  

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





