#!/bin/bash

# resides in the ami with the server application
# The AWS Lambda waits for this to run succesfully THEN it calls for i=the instance to be terminated
# This script ensures the data is saved first

# Tell the server to save all data
tmux send-keys -t mc "save-all flush" Enter
sleep 5

# Upload world folder to S3 (replace with your bucket)
aws s3 sync /home/ubuntu/minecraft/world s3://greysminecraftserver/world --delete
aws s3 sync /home/ubuntu/minecraft/world_nether s3://greysminecraftserver/world_nether --delete
aws s3 sync /home/ubuntu/minecraft/world_the_end s3://greysminecraftserver/world_the_end --delete
