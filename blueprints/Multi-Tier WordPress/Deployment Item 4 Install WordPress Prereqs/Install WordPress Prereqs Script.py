#!/bin/sh

python -mplatform | grep -qi ubuntu
is_ubuntu=$?

setenforce 0

if [ $is_ubuntu -eq 0 ]; then
    apt-get install apache2 php5 libapache2-mod-php5 php5-mysql php5-mysqlnd curl debconf-utils
else
    #yum install https://dl.fedoraproject.org/pub/epel/epel-release-latest-7.noarch.rpm
    #yum install http://rpms.remirepo.net/enterprise/remi-release-7.rpm
    #yum install yum-utils
    #yum-config-manager --enable remi-php56   [Install PHP 5.6]
    yum install -y httpd php php-common php-mysql php-gd php-xml php-mbstring php-mcrypt libjpeg-turbo
    
    #rpm -Uvh http://vault.centos.org/7.0.1406/extras/x86_64/Packages/epel-release-7-5.noarch.rpm
    #rpm -Uvh http://rpms.famillecollet.com/enterprise/remi-release-7.rpm
    #yum --enablerepo=remi,remi-php56 install php php-common
    #yum --enablerepo=remi,remi-php56 install php-cli php-pear php-pdo php-mysql php-mysqlnd php-pgsql php-sqlite php-gd php-mbstring php-mcrypt php-xml php-simplexml php-curl php-zip
    #yum install -y httpd libjpeg-turbo
    echo service restart
    service httpd restart
fi