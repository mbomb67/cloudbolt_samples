variable "resource_group_name" {
  type = string
  default = "se-demo"
}

variable "storage_account_name" {
  type = string
  default = "mbcbtestaccount1"
}

variable "storage_container_name" {
  type = string
  default = "mbcbtestcontainer1"
}

variable "account_replication_type" {
  type = string
  default = "LRS"
}

variable "global_tags" {
  type = map(string)
  default = {
    cost_center = "8943756"
    owner       = "mbombard@cloudbolt.io"
    group       = "IT"
  }
}