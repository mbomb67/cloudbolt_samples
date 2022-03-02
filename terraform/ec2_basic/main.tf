resource "aws_instance" "server" {
    ami = var.ami_id
    instance_type = var.instance_type
    availability_zone = var.availability_zone

    tags = {
        Name = var.ec2_name
        group = var.group
        owner = var.owner
    }
}