import boto3
import time

REGION = "us-east-1"
LAMBDA_SHUTDOWN = "SaveWorldShutdown"  # your shutdown lambda name
ACCOUNT_ID = "00000000000"                   # your AWS account ID
LAUNCH_TEMPLATE_NAME = "LaunchMCServer"       # your template name

ec2 = boto3.client('ec2', region_name=REGION)
cloudwatch = boto3.client('cloudwatch', region_name=REGION)

def lambda_handler(event, context):
    # 1️⃣ Look for existing running or pending Minecraft instance
    running = ec2.describe_instances(
        Filters=[
            {'Name': 'tag:MinecraftServer', 'Values': ['True']},
            {'Name': 'instance-state-name', 'Values': ['pending', 'running']}
        ]
    )

    if any(r['Instances'] for r in running.get('Reservations', [])):
        print("Minecraft server already running or pending. Skipping new launch.")
        return {"status": "already-running"}

    # 2️⃣ Launch new instance
    print("Launching new Minecraft server from template...")
    response = ec2.run_instances(
        LaunchTemplate={'LaunchTemplateName': LAUNCH_TEMPLATE_NAME},
        MinCount=1,
        MaxCount=1
    )

    instance_id = response['Instances'][0]['InstanceId']
    print(f"[OK] Launched instance: {instance_id}")

    # 3️⃣ Tag instance so proxy can detect it
    ec2.create_tags(Resources=[instance_id], Tags=[
        {'Key': 'MinecraftServer', 'Value': 'True'}
    ])

    # 4️⃣ Wait until running before adding alarm
    print("[WAIT] Waiting for instance to enter 'running' state...")
    waiter = ec2.get_waiter('instance_running')
    waiter.wait(InstanceIds=[instance_id])
    print("[OK] Instance is running — creating CloudWatch alarm")

    # 5️⃣ Create a CloudWatch alarm to auto-shutdown after 3 min of inactivity
    alarm_name = f"AutoShutdown-{instance_id}"
    cloudwatch.put_metric_alarm(
        AlarmName=alarm_name,
        Namespace="MinecraftServer",
        MetricName="ActivePlayers",
        Dimensions=[{'Name': 'InstanceId', 'Value': 'shared'}],
        Statistic="Average",
        Period=60,                 # 1 minute periods
        EvaluationPeriods=3,       # 3 mins total
        Threshold=1,
        ComparisonOperator="LessThanThreshold",
        TreatMissingData="notBreaching",
        ActionsEnabled=True,
        AlarmActions=[
            f"arn:aws:sns:{REGION}:{ACCOUNT_ID}:MinecraftShutdownTopic"
        ],
        AlarmDescription="Shut down Minecraft instance after 3 minutes of inactivity"
    )

    print(f"[OK] CloudWatch alarm created: {alarm_name}")

    return {"status": "started", "instance_id": instance_id}
