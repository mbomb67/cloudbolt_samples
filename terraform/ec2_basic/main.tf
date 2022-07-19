resource "aws_instance" "server" {
    ami = var.ami_id
    instance_type = var.instance_type
    subnet_id = var.subnet_id
    key_name = var.key_name

    tags = {
        Name = var.ec2_name
        group = var.group
        owner = var.owner
    }
}