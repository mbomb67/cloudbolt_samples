resource "aws_instance" "server" {
    ami = var.ami_id
    instance_type = var.instance_type
    subnet_id = var.subnet_id

    tags = {
        Name = var.ec2_name
        group = var.owner_group.owner
        owner = var.owner_group.group
    }
}