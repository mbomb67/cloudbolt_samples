#!/bin/bash
TF_COMMAND="/var/opt/cloudbolt/terraform/bin/terraform_1.1.7"
CONFIG_PATH="${@: -1}"
ARGS_STRING="${@:1:$#-1}"

if [[ "$1" = "apply" ]]
then
  SCRIPT_COMMAND="$TF_COMMAND $ARGS_STRING $CONFIG_PATH"
else
  SCRIPT_COMMAND="$TF_COMMAND -chdir=$CONFIG_PATH $ARGS_STRING"
fi
echo $SCRIPT_COMMAND
eval $SCRIPT_COMMAND
