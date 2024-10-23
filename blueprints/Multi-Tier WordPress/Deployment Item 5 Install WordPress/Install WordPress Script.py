#!/bin/sh

# Original script taken from https://gist.github.com/bgallagh3r/2853221 and modified
#yum install libselinux-utils -y
setenforce 0 

echo "============================================"
echo "WordPress Install Script"
echo "============================================"
dbname='{{server.database_name}}'
dbuser='{{server.database_username}}'
dbpass='{{server.database_password}}'
dbhost='{{blueprint_context.db.server.ip}}'

echo "DB Host set to: $dbhost"

# download wordpress

#curl -O https://wordpress.org/latest.tar.gz
curl -O https://wordpress.org/wordpress-5.1.11.tar.gz

# unzip wordpress

#tar -zxf latest.tar.gz
tar -zxf wordpress-5.1.11.tar.gz

# change dir to wordpress
cd wordpress

#create wp config
cp wp-config-sample.php wp-config.php

#set database details with perl find and replace
perl -pi -e "s/database_name_here/$dbname/g" wp-config.php
perl -pi -e "s/username_here/$dbuser/g" wp-config.php
perl -pi -e "s/password_here/$dbpass/g" wp-config.php
perl -pi -e "s/password_here/$dbpass/g" wp-config.php
perl -pi -e "s/localhost/$dbhost/g" wp-config.php

#set WP salts
perl -i -pe'
  BEGIN {
    @chars = ("a" .. "z", "A" .. "Z", 0 .. 9);
    push @chars, split //, "!@#$%^&*()-_ []{}<>~\`+=,.;:/?|";
    sub salt { join "", map $chars[ rand @chars ], 1 .. 64 }
  }
  s/put your unique phrase here/salt()/ge
' wp-config.php

#create uploads folder and set permissions

mkdir -p wp-content/uploads
chmod 775 wp-content/uploads

echo "Copying WordPress files to /var/www/html..."
cp -R ~/wordpress/* /var/www/html/
chown -R apache:apache /var/www/html/
# firewall-cmd --permanent --zone=public --add-service=http
# firewall-cmd --permanent --zone=public --add-service=https
# firewall-cmd --reload
systemctl restart httpd

echo "Cleaning..."
rm ../wordpress-5.1.11.tar.gz

echo "========================="
echo "Installation is complete."
echo "========================="