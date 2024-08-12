#!/bin/bash

# Function to install Java
install_java() {
    sudo dnf install java-11-openjdk-devel -y
}

# Function to install and configure Elasticsearch
install_elasticsearch() {
    sudo dnf install -y dnf-utils
    sudo rpm --import https://artifacts.elastic.co/GPG-KEY-elasticsearch
    sudo bash -c 'cat <<EOT > /etc/yum.repos.d/elasticsearch.repo
[elasticsearch-8.x]
name=Elasticsearch repository for 8.x packages
baseurl=https://artifacts.elastic.co/packages/8.x/yum
gpgcheck=1
gpgkey=https://artifacts.elastic.co/GPG-KEY-elasticsearch
enabled=1
autorefresh=1
type=rpm-md
EOT'

    sudo dnf install elasticsearch -y
    sudo systemctl enable elasticsearch
    sudo systemctl start elasticsearch
}

# Function to install and configure Logstash
install_logstash() {
    sudo dnf install logstash -y
    sudo systemctl enable logstash
    sudo systemctl start logstash
}

# Function to install and configure Kibana
install_kibana() {
    sudo dnf install kibana -y

    # Update Kibana configuration
    sudo bash -c 'cat <<EOT >> /etc/kibana/kibana.yml
server.port: 5601
server.host: "0.0.0.0"
EOT'

    sudo systemctl enable kibana
    sudo systemctl start kibana
}

# Function to configure firewall
configure_firewall() {
    sudo firewall-cmd --permanent --add-port=9200/tcp
    sudo firewall-cmd --permanent --add-port=5601/tcp
    sudo firewall-cmd --reload
}

# Function to verify services
verify_services() {
    if systemctl status elasticsearch | grep "active (running)" > /dev/null &&
       systemctl status logstash | grep "active (running)" > /dev/null &&
       systemctl status kibana | grep "active (running)" > /dev/null
    then
        echo "Elastic Stack is installed and running."
        echo "Elasticsearch is running on port 9200."
        echo "Kibana is accessible at http://$(hostname -I | awk '{print $1}'):5601/"
    else
        echo "Elastic Stack installation or startup failed."
    fi
}

# Main script execution
install_java
install_elasticsearch
install_logstash
install_kibana
configure_firewall
verify_services