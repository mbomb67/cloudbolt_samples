#!/bin/bash
setenforce 0

dbname='{{server.database_name}}'
dbuser='{{server.database_username}}'
dbpass='{{server.database_password}}'
mysqlRootPass='{{server.database_password}}'
webhost='{{blueprint_context.web.server.ip}}'

echo ' -> Removing previous mysql server installation'
systemctl stop mysqld.service && yum remove -y mysql-community-server && rm -rf /var/lib/mysql && rm -rf /var/log/mysqld.log && rm -rf /etc/my.cnf

echo ' -> Installing mysql server (community edition)'
yum localinstall -y https://dev.mysql.com/get/mysql57-community-release-el7-7.noarch.rpm
yum install -y mysql-community-server

echo ' -> Starting mysql server (first run)'
systemctl enable mysqld.service
systemctl start mysqld.service
tempRootDBPass="`grep 'temporary.*root@localhost' /var/log/mysqld.log | tail -n 1 | sed 's/.*root@localhost: //'`"

echo $tempRootDBPass

echo ' -> Setting up new mysql server root password'
systemctl stop mysqld.service
rm -rf /var/lib/mysql/*logfile*
#wget -O /etc/my.cnf "https://my-site.com/downloads/mysql/512MB.cnf"
systemctl start mysqld.service

echo ' -> mysqladmin'
mysqladmin -u root --password="$tempRootDBPass" password "$mysqlRootPass"

echo ' -> mysqladmin eosql'

mysql -u root --password="$mysqlRootPass" -e"
    DELETE FROM mysql.user WHERE User='';
    DROP DATABASE IF EXISTS test;
    DELETE FROM mysql.db WHERE Db='test' OR Db='test\\_%';
    DELETE FROM mysql.user where user != 'mysql.sys';
    CREATE USER 'root'@'%' IDENTIFIED BY '${mysqlRootPass}';
    GRANT ALL ON *.* TO 'root'@'%' WITH GRANT OPTION;
    FLUSH PRIVILEGES;"
systemctl status mysqld.service
echo " -> MySQL server installation completed, root password: $mysqlRootPass";

echo "============================================"
echo "Create WordPress Database"
echo "============================================"
mysql -u root --password="${dbpass}" -e"
  UNINSTALL PLUGIN validate_password;"

echo "Creating database with the name '$dbname'..."
mysql -u root --password="${dbpass}" -e"
  CREATE DATABASE $dbname;
  CREATE USER ${dbuser}@${webhost} IDENTIFIED BY '${dbpass}';
  GRANT ALL PRIVILEGES ON $dbname.* TO ${dbuser}@${webhost} IDENTIFIED BY '${dbpass}';
  FLUSH PRIVILEGES;"

mysql -u root --password="${dbpass}" -e"
  INSTALL PLUGIN validate_password SONAME 'validate_password.so';"