variable "owner" {
  type = string
  default = "mikeb"
}

variable "group" {
  type = string
  default = "IT"
}

variable "ami_id" {
  type = string
  default = "ami-0aa7d40eeae50c9a9"
}

variable "instance_type" {
  type = string
  default = "t3.micro"
}

variable "ec2_name" {
  type = string
  default = "mb-tst-123"
}

variable "subnet_id" {
  type = string
  default = "subnet-c736068a"
}
