# Patch Kit

"Patch Kit" is a Django app that provides some structure for deploying dynamic runtime patches (aka Monkey Patches) for
a given CloudBolt instance.

## Installing Patch Kit

In order to add Patch Kit to your CloudBolt instance so that it is loaded on start-up, after copying this directory (
patch_kit) to `/var/opt/cloudbolt/proserv/`, you'll add the following line
to `/var/opt/cloudbolt/proserv/customer_settings.py`:

```python
from settings import INSTALLED_APPS, PROSERV_DIR

INSTALLED_APPS += ('patch_kit.apps.PatchKitConfig',)
```

After adding these lines, restart your CloudBolt instance with: `systemctl restart httpd`

## About Dynamic Patches

Sometimes there's just a small tweak that is needed to bring a customer/prospect CB instance to heel.

## Using Patch Kit

Patches are organized into Patch Sets and are stored in the `patch_kit/patch_sets` sub-directory with a filename
matching ps_*.py. For instance if I have a Patch Set for some changes for CustomerX I might name this Patch
Set `ps_customer_x.py`.
