module "ec2_module" {
    source = "github.com/mbomb67/mb_terraform_configs.git"
    ami_id = var.ami_id
    instance_type = var.instance_type
    subnet_id = var.subnet_id
    associate_public_ip_address = false
    ec2_name = var.ec2_name
    group = var.group
    owner = var.owner
}
