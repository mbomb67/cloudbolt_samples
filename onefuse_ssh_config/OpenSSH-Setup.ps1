<#

WARRANTY/GUARANTEE

This script is provided as is without support, warranty or guarantee.  

SCRIPT PURPOSE

This script was created to help automate the OpenSSH implementation onto a Windows 2016 server. 

- Set your TLS to 1/1.1/1.2
- Download the OpenSSH from the official Microsoft source on Github (https://github.com/PowerShell/Win32-OpenSSH/releases/latest/download/OpenSSH-Win64.zip) as outlined in the following microsoft article
  https://docs.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse
- Expand the archive and copy the OpenSSH Binaries to c:\windows
- Update your server environment variables and append the path for OpenSSH
- Install openSSH and accept the certificate from Microsoft
- Will check and set the OpenSSH service is set to Automatic and Running
- Will fix file permissions for OpenSSH
- Will check for existing firewall rules and create them if required


WRITTEN BY: Denis Duri
DATE: 20/04/2020
EMAIL:  dduri@sovlabs.com
VERSION: 1.0


OpenSSH version tested with: v8.1.0.0
OpenSSH version release date: 17th December 2019

#>



# Set TLS protocols
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls12

#Download file from GitHub and extract
$TempFolder = "$Env:temp"

write-host "Downloading file from https://github.com/PowerShell/Win32-OpenSSH/releases/latest/download/OpenSSH-Win64.zip"
Invoke-WebRequest -Uri "https://github.com/PowerShell/Win32-OpenSSH/releases/latest/download/OpenSSH-Win64.zip" -OutFile "$tempfolder\OpenSSH-Win64.zip"

Expand-Archive -LiteralPath $tempfolder\OpenSSH-Win64.zip -DestinationPath c:\Windows\

#Update Environment Variable to include C:\Windows\OpenSSH-Win64

$OpenSSH = ";c:\windows\OpenSSH-Win64"
cd HKLM:\
set-location -path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment\'
Get-ChildItem
$RegKeyUpdate = Get-ItemProperty -path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment\' | select-object Path -ExpandProperty path
$RegKeyUpdate
$RegKeyUpdate = $RegKeyUpdate + $OpenSSH
$RegKeyUpdate
Set-ItemProperty -path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment\' -name path -value $RegKeyUpdate
Get-ItemProperty -path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment\' -name path | select-object path -ExpandProperty Path
write-host "exiting HKLM Key"
cd\

#Run the install-sshd.ps1 to add ssh to the service. You will have to accept the untrusted publisher
powershell.exe -executionpolicy bypass -c ". 'C:\windows\OpenSSH-Win64\install-sshd.ps1' ;"

#Check the services are running, if not, set to auto and running

$ServiceSetup = get-service -name sshd | select-object Name, DisplayName,Status, StartType
write-host "Gathering information on the ssh service to ensure it is set to automatic and running. If it is not, will set to automatic and running"

if ($serviceSetup.status -eq "Stopped" -or $ServiceSetup.StartType -eq "Manual")
    {
    write-host "The"$ServiceSetup.Name "Service is currently" $ServiceSetup.Status "and set to" $ServiceSetup.StartType
    write-host "We will set this to running and automatic"
    Get-Service -Name sshd | Set-Service -StartupType Automatic -Status Running
    $ServiceSetup = get-service -name sshd | select-object Name, DisplayName,Status, StartType
    write-host "The"$ServiceSetup.Name "Service is now set to" $ServiceSetup.Status "and the start type is" $ServiceSetup.StartType
    }
Else
    {
    write-host $ServiceSetup.Name" is running and set to "$ServiceSetup.startType "No changes needed"
    }

# Run the Permissions Fix as per article, you will have to hit YES to fix permissions

C:\windows\OpenSSH-Win64\FixHostFilePermissions.ps1 -confirm:$false

# Create an inbound firewall rule

write-host "Checking firewall rules to confirm if port 22 is already open for SSH, If not will go ahead and create the rule"

$ListFirewallRules = Get-NetFirewallRule | Select-object Name,DisplayName,DisplayGroup,
@{Name='Protocol';Expression={($PSItem | Get-NetFirewallPortFilter).Protocol}},
@{Name='LocalPort';Expression={($PSItem | Get-NetFirewallPortFilter).LocalPort}},
Enabled,Direction,Action

$MyOpenSSHRule = $ListFireWallRules | Where-object {$_.localport -eq "22" -and $_.Direction -eq "Inbound" -and $_.Protocol -eq "TCP"}
if ($MyOpenSSHRule -eq $Null)
    {
    write-host "There are no rules that match Port: 22 using TCP and Inbound.  We will create this rule now"
    New-NetFirewallRule -Name "sshd" -DisplayName "OpenSSH Server (sshd)" -direction Inbound -LocalPort 22 -Protocol TCP -Action Allow
    write-host "Firewall rule has been created"

    Get-NetFirewallRule -Name "sshd" | Select-object Name,DisplayName,DisplayGroup,
@{Name='Protocol';Expression={($PSItem | Get-NetFirewallPortFilter).Protocol}},
@{Name='LocalPort';Expression={($PSItem | Get-NetFirewallPortFilter).LocalPort}},
Enabled,Direction,Action | FT
    }
else
    {
    write-host "Rule already exists, skipping creation"
    }

# Remove the ZIP file that was downloaded

$FileLocation = "$tempfolder\OpenSSH-Win64.zip"

write-host "Deleting the following file: " $FileLocation

remove-item $FileLocation

