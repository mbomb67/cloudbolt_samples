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
  default = "ami-097e7e9ff9678704a"
}

variable "instance_type" {
  type = string
  default = "t3.micro"
}

variable "ec2_name" {
  type = string
  default = "mb-tst-123"
}

variable "availability_zone" {
  type = string
  default = "us-east-2a"
}