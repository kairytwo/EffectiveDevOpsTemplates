from troposphere import (
        Base64,
        ec2,
        GetAtt,
        Join,
        Output,
        Parameter,
        Ref,
        Template,
        elasticloadbalancing as elb
)

from troposphere.iam import (
    InstanceProfile,
    PolicyType as IAMPolicy,
    Role,
)

from troposphere.autoscaling import (
    AutoScalingGroup,
    LaunchConfiguration,
    ScalingPolicy
)

from troposphere.cloudwatch import (
    Alarm,
    MetricDimension
)

from awacs.aws import (
    Action,
    Allow,
    Policy,
    Principal,
    Statement,
)

from awacs.sts import AssumeRole

from pprint import pprint

ApplicationName = "nodeserver"
ApplicationPort = "3000"

GithubAccount = "kairytwo"
GithubAnsibleURL = "https://github.com/kairytwo/Ansible".format(GithubAccount)

AnsiblePullCmd = "/usr/local/bin/ansible-pull -U {} {}.yml -i localhost".format(GithubAnsibleURL, ApplicationName)

t = Template()

t.add_description('Effective DevOps')
t.add_parameter(Parameter("KeyPair", Description="Name of KeyPair", Type="AWS::EC2::KeyPair::KeyName",
                          ConstraintDescription="must be the name of an existing EC2 KeyPair"))

t.add_parameter(Parameter("VpcId", Type="AWS::EC2::VPC::Id", Description="VPC"))

t.add_parameter(Parameter("PublicSubnet", Type="List<AWS::EC2::Subnet::Id>", ConstraintDescription="PublicSubnet"))

t.add_parameter(Parameter("ScaleCapacity", Default="3", Type="String", Description="Number servers to run"))

t.add_parameter(Parameter("InstanceType", Default="t2.micro", Type="String", Description="instance type",
    AllowedValues=[
        "t2.micro",
        "t2.small",
        "t2.medium",
        "t2.large"
    ]
))

t.add_resource(ec2.SecurityGroup(
    "LoadBalancerSecurityGroup",
    GroupDescription="Web Load balancer security group",
    VpcId=Ref("VpcId"),
    SecurityGroupIngress=[
        ec2.SecurityGroupRule(
            IpProtocol="tcp",
            FromPort="3000",
            ToPort="3000",
            CidrIp="0.0.0.0/0"
        )
    ]
))

t.add_resource(elb.LoadBalancer(
    "LoadBalancer",
    Scheme="internet-facing",
    Listeners=[
        elb.Listener(
            LoadBalancerPort=ApplicationPort,
            InstancePort="3000",
            Protocol="HTTP",
            InstanceProtocol="HTTP"
        )
    ],
    HealthCheck=elb.HealthCheck(
        Target="HTTP:3000/",
        HealthyThreshold="5",
        UnhealthyThreshold="2",
        Interval="20",
        Timeout="15"
    ),
    ConnectionDrainingPolicy=elb.ConnectionDrainingPolicy(
        Enabled=True,
        Timeout=10
    ),
    CrossZone=True,
    Subnets=Ref("PublicSubnet"),
    SecurityGroups=[Ref("LoadBalancerSecurityGroup")]
))

t.add_resource(ec2.SecurityGroup("SecurityGroup",
    GroupDescription="Allow SSH and TCP/{} access".format(ApplicationPort),
    SecurityGroupIngress=[
        ec2.SecurityGroupRule(
            IpProtocol="tcp",
            FromPort="22",
            ToPort="22",
            CidrIp="0.0.0.0/0"
        ),
        ec2.SecurityGroupRule(
            IpProtocol="tcp",
            FromPort=ApplicationPort,
            ToPort=ApplicationPort,
            CidrIp="0.0.0.0/0"
        )
    ],
    VpcId=Ref("VpcId")
))

ud = Base64(Join('\n',[
    '#! /bin/bash',
    'yum install -y git;',
    'pip install ansible;',
    AnsiblePullCmd,
    "echo '*/10 * * * * {}' > /etc/cron.d/ansible-pull".format(AnsiblePullCmd)

]))

t.add_resource(Role(
    "Role",
    AssumeRolePolicyDocument=Policy(
        Statement=[
            Statement(
                Effect=Allow,
                Action=[AssumeRole],
                Principal=Principal("Service", ["ec2.amazonaws.com"])
            )
        ]
    )
))

t.add_resource(InstanceProfile(
    "InstanceProfile",
    Path="/",
    Roles=[Ref("Role")]
))

t.add_resource(IAMPolicy(
    "Policy",
    PolicyName="AllowS3",
    PolicyDocument=Policy(
        Statement=[
            Statement(
                Effect=Allow,
                Action=[Action("s3", "*")],
                Resource=["*"]
            )
        ]
    ),
    Roles=[Ref("Role")]
))

t.add_resource(LaunchConfiguration(
    "LaunchConfiguration",
    UserData=ud,
    ImageId="ami-a4c7edb2",
    KeyName=Ref("KeyPair"),
    SecurityGroups=[Ref("SecurityGroup")],
    InstanceType=Ref("InstanceType"),
    IamInstanceProfile=Ref("InstanceProfile")
))

t.add_resource(AutoScalingGroup(
    "AutoScalingGroup",
    DesiredCapacity=Ref("ScaleCapacity"),
    LaunchConfigurationName=Ref("LaunchConfiguration"),
    MinSize=2,
    MaxSize=5,
    LoadBalancerNames=[Ref("LoadBalancer")],
    VPCZoneIdentifier=Ref("PublicSubnet")
))

t.add_resource(ScalingPolicy(
    "ScaleDownPolicy",
    ScalingAdjustment="-1",
    AutoScalingGroupName=Ref("AutoScalingGroup"),
    AdjustmentType="ChangeInCapacity"
))

t.add_resource(ScalingPolicy(
    "ScaleUpPolicy",
    ScalingAdjustment="1",
    AutoScalingGroupName=Ref("AutoScalingGroup"),
    AdjustmentType="ChangeInCapacity"
))

t.add_resource(Alarm(
    "CPUTooLow",
    AlarmDescription="Alarm if CPU too low",
    Namespace="AWS/EC2",
    MetricName="CPUUtilization",
    Dimensions=[
        MetricDimension(
            Name="AutoScalingGroupName",
            Value=Ref("AutoScalingGroup")
        )
    ],
    Statistic="Average",
    Period="60",
    EvaluationPeriods="1",
    Threshold="30",
    ComparisonOperator="LessThanThreshold",
    AlarmActions=[Ref("ScaleDownPolicy")]
))

t.add_resource(Alarm(
    "CPUTooHigh",
    AlarmDescription="Alarm if CPU too high",
    Namespace="AWS/EC2",
    MetricName="CPUUtilization",
    Dimensions=[
        MetricDimension(
            Name="AutoScalingGroupName",
            Value=Ref("AutoScalingGroup")
        )
    ],
    Statistic="Average",
    Period="60",
    EvaluationPeriods="1",
    Threshold="60",
    ComparisonOperator="GreaterThanThreshold",
    AlarmActions=[Ref("ScaleUpPolicy")],
    InsufficientDataActions=[Ref("ScaleUpPolicy")]
))

t.add_output(Output(
    "WebUrl",
    Description="Application endpoint",
    Value=Join("", [
        "http://", GetAtt("LoadBalancer", "DNSName"), ":", ApplicationPort
    ])
))

print(t.to_json())


