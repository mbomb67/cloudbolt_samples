from importlib import import_module
from django.apps import AppConfig
from settings import PROSERV_DIR

if __name__ == '__main__':
    import django

    django.setup()


class PatchKitConfig(AppConfig):
    name = 'patch_kit'

    def ready(self):
        import_patches()


def import_patches():
    import os
    patch_sets = filter(
        lambda i: i.startswith('ps_'),
        os.listdir(f'{PROSERV_DIR}/patch_kit/patch_sets'))
    patch_sets = sorted(list(patch_sets))

    for patch_set_file in patch_sets:
        print(f'applying patch set: {patch_set_file}')
        patch_set_name = patch_set_file.rsplit('.')[0]
        module_name = f'patch_kit.patch_sets.{patch_set_name}'
        mod = import_module(module_name)
        patch_functions = filter(lambda i: i.startswith('patch_'), dir(mod))
        sorted_patch_functions = sorted(list(patch_functions))

        for func in sorted_patch_functions:
            print(f"\tapplying patch: {func}")
            getattr(mod, func)()


def main():
    pass


if __name__ == '__main__':
    main()
