import boto3
from botocore.exceptions import ClientError

#AUTOSCALING GROUP

def create_auto_scaling_group(asg_client, ec2_client, elbv2_client):
    """Creates an auto scaling group based on user input."""
    # Input for Auto Scaling group name
    asg_name = input("Enter the name for the Auto Scaling group: ").strip()

    # List and choose or create a new launch template
    response = ec2_client.describe_launch_templates()
    launch_templates = response['LaunchTemplates']
    if launch_templates:
        print("Available Launch Templates:")
        for lt in launch_templates:
            print(f"- {lt['LaunchTemplateName']} (ID: {lt['LaunchTemplateId']})")

        use_existing = input("Use existing launch template? (yes/no): ").strip().lower()
        if use_existing == 'yes':
            launch_template_id = input("Enter the launch template ID: ").strip()
        else:
            launch_template_id = create_launch_template(ec2_client)
    else:
        print("No existing launch templates found. You need to create a new one.")
        launch_template_id = create_launch_template(ec2_client)

    if not launch_template_id:
        print("Failed to create or select a launch template. Exiting...")
        return

    # Select network options
    vpc_id = input("Enter the VPC ID: ").strip()
    subnet_map = list_subnets(ec2_client, vpc_id)
    if not subnet_map:
        print("Failed to retrieve subnets. Exiting...")
        return

    print("Available Subnets by Availability Zone:")
    for az, subnet_ids in subnet_map.items():
        print(f"- {az}: {', '.join(subnet_ids)}")
    
    selected_subnet_ids = input(f"Enter the Subnet IDs (comma-separated) from the available options: ").strip().split(',')

    # Advanced options
    lb_option = input("Choose load balancing option (no load balancer/existing load balancer/new load balancer): ").strip().lower()
    load_balancer_arn = None
    if lb_option == 'existing load balancer':
        load_balancers = elbv2_client.describe_load_balancers()['LoadBalancers']
        print("Available Load Balancers:")
        for lb in load_balancers:
            print(f"- {lb['LoadBalancerName']} (ARN: {lb['LoadBalancerArn']})")
        load_balancer_arn = input("Enter the load balancer ARN: ").strip()

        if not validate_load_balancer_with_subnets(elbv2_client, load_balancer_arn, selected_subnet_ids, vpc_id):
            print("Please ensure the selected load balancer is associated with the selected subnets and VPC.")
            return

    # Health check configuration
    health_check_type = input("Enter the health check type (EC2/ELB): ").strip().upper()
    health_check_grace_period = int(input("Enter the health check grace period (in seconds): ").strip())

    # Configure group size
    min_size = int(input("Enter the minimum group size: ").strip())
    max_size = int(input("Enter the maximum group size: ").strip())
    desired_capacity = int(input("Enter the desired capacity: ").strip())

    try:
        asg_client.create_auto_scaling_group(
            AutoScalingGroupName=asg_name,
            LaunchTemplate={
                'LaunchTemplateId': launch_template_id,
            },
            MinSize=min_size,
            MaxSize=max_size,
            DesiredCapacity=desired_capacity,
            VPCZoneIdentifier=",".join(selected_subnet_ids),
            HealthCheckType=health_check_type,
            HealthCheckGracePeriod=health_check_grace_period,
            LoadBalancerNames=[load_balancer_arn] if lb_option == 'existing load balancer' else []
        )
        print(f"Auto Scaling group '{asg_name}' created successfully.")
    except Exception as e:
        print(f"Error creating Auto Scaling group: {e}")
        print("Please re-enter the values correctly.")

def list_subnets(ec2_client, vpc_id):
    """Lists subnets by availability zone in the specified VPC."""
    try:
        subnets = ec2_client.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])['Subnets']
        subnet_map = {}
        for subnet in subnets:
            az = subnet['AvailabilityZone']
            subnet_id = subnet['SubnetId']
            if az not in subnet_map:
                subnet_map[az] = []
            subnet_map[az].append(subnet_id)
        return subnet_map
    except Exception as e:
        print(f"Error listing subnets: {e}")
        return {}

#LOAD BALANCER 

def create_load_balancer(elbv2_client, ec2_client):
    """Creates a load balancer based on user input."""
    lb_type = input("Enter load balancer type (Application/Network/Gateway): ").strip().lower()
    if lb_type not in ['application', 'network', 'gateway']:
        print("Invalid load balancer type.")
        return

    name = input("Enter the name of the load balancer: ").strip()

    # Scheme can be 'internet-facing' or 'internal'
    scheme = input("Enter the scheme (internet-facing/internal): ").strip().lower()
    if scheme not in ['internet-facing', 'internal']:
        print("Invalid scheme.")
        return

    ip_type = 'ipv4'
    if lb_type == 'network':
        ip_type = input("Enter the IP type (ipv4/dualstack): ").strip().lower()
        if ip_type not in ['ipv4', 'dualstack']:
            print("Invalid IP type.")
            return

    # List VPCs and get user input
    list_vpcs(ec2_client)
    vpc_id = input("Enter the VPC ID: ").strip()

    # List Subnets and get user input
    subnets = list_subnets(ec2_client, vpc_id)
    if not subnets:
        print("No valid subnets found for the VPC.")
        return

    while True:
        selected_subnets = input(f"Enter the Subnet IDs to use (comma-separated from {subnets}): ").strip().split(',')
        selected_subnets = [s.strip() for s in selected_subnets]
        if all(subnet in subnets for subnet in selected_subnets):
            break
        print("Some subnet IDs are invalid. Please enter valid subnet IDs.")

    # List Target Groups and get user input
    list_target_groups(elbv2_client)
    target_group_arn = input("Enter the target group ARN: ").strip()
    if not validate_target_group_arn(elbv2_client, target_group_arn):
        print("Invalid Target Group ARN.")
        return

    # List Security Groups and get user input
    security_groups = ec2_client.describe_security_groups()
    print("Available Security Groups:")
    for sg in security_groups['SecurityGroups']:
        print(f"- {sg['GroupName']} (ID: {sg['GroupId']})")
    security_group_ids = input("Enter the security group IDs (comma-separated): ").strip().split(',')
    if not all(sg_id in [sg['GroupId'] for sg in security_groups['SecurityGroups']] for sg_id in security_group_ids):
        print("Some security group IDs are invalid.")
        return

    # Create Load Balancer
    try:
        load_balancer = elbv2_client.create_load_balancer(
            Name=name,
            Subnets=selected_subnets,
            SecurityGroups=security_group_ids if lb_type == 'application' else [],
            Scheme=scheme,
            Type=lb_type,
            IpAddressType=ip_type
        )
        lb_arn = load_balancer['LoadBalancers'][0]['LoadBalancerArn']
        print(f"Load balancer '{name}' created with ARN: {lb_arn}")
    except elbv2_client.exceptions.InvalidSubnetException:
        print("Invalid subnet ID provided.")
        return

    # Create Listeners & Routing
    while True:
        protocol = input("Enter listener protocol (HTTP/HTTPS/TCP): ").strip().upper()
        if protocol not in ['HTTP', 'HTTPS', 'TCP']:
            print("Invalid protocol.")
            continue

        try:
            port = int(input("Enter listener port (e.g., 80): ").strip())
        except ValueError:
            print("Invalid port number.")
            continue

        try:
            listener = elbv2_client.create_listener(
                LoadBalancerArn=lb_arn,
                Protocol=protocol,
                Port=port,
                DefaultActions=[{
                    'Type': 'forward',
                    'TargetGroupArn': target_group_arn
                }]
            )
            print(f"Listener created with ARN: {listener['Listeners'][0]['ListenerArn']}")
        except elbv2_client.exceptions.InvalidConfigurationRequestException:
            print("Invalid configuration for listener.")
            continue

        another_listener = input("Add another listener? (yes/no): ").strip().lower()
        if another_listener != 'yes':
            break

    return lb_arn
def validate_load_balancer_with_subnets(elbv2_client, load_balancer_arn, subnets, vpc_id):
    """Checks if the provided load balancer is associated with the correct subnets and VPC."""
    try:
        lb_details = elbv2_client.describe_load_balancers(LoadBalancerArns=[load_balancer_arn])
        lb = lb_details['LoadBalancers'][0]

        # Validate VPC
        if lb['VpcId'] != vpc_id:
            print(f"The load balancer is not associated with the provided VPC ID '{vpc_id}'.")
            return False

        # Validate Subnets
        lb_subnet_ids = [az['SubnetId'] for az in lb['AvailabilityZones']]
        for subnet in subnets:
            if subnet not in lb_subnet_ids:
                print(f"Subnet ID '{subnet}' is not associated with the selected load balancer.")
                return False

        return True
    except Exception as e:
        print(f"Error validating load balancer with subnets: {e}")
        return False

def main():
    ec2_client = boto3.client('ec2')
    elbv2_client = boto3.client('elbv2')
    asg_client = boto3.client('autoscaling')

    action = input("Enter action (autoscaling group): ").strip().lower()

if action == "autoscaling group":
        create_auto_scaling_group(asg_client, ec2_client, elbv2_client)
    else:
        print("Invalid action. Please enter valid service: .")

if __name__ == "__main__":
    main()
