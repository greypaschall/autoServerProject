AWS Stateless Minecraft Server
-------------------------------

This project has been my introduction to automation basics and cloud hosting services. Using the foundational knowledge I gained while studying for my AWS solutions architect certification, I set out to build the cheapest possible Minecraft server, with minimal overhead and no manual upkeep. This system can spin up only when needed and stay off when idle. 

(NOTE)
This project is *not* a turnkey “one-click deploy” solution. It contains the
> scripts and Lambda functions I run in my own AWS account. I decided against a direct
> CloudFormation deployment link so that anyone using this can first inspect the code, review
> the IAM permissions, and decide how they want to integrate it into their own environment.

----------------------------------------
Flow:

-Player connects to listener IP

-Ingame they will see a motd saying "Starting up please wait ~2 minutes"

-Listener watches for valid TCP handshake and calls AWS Lambda function to start the server from a launch template

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





