"""
Plugin to validate OpenStack requirements on an order form for OpenStack
resources.

This plugin:
- Validates that the requested disk size meets the minimum requirements of
    the selected OS build.
- Validates that CPU and Memory sizes are provided when using a hotplug flavor.

"""
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def validate_order_form(  # noqa: E302
    profile,
    group,
    env,
    quantity,
    hostname,
    cfvs,
    pcvss,
    os_build=None,
    build_item=None,
    **kwargs,
):
    """
    Plugin to validate disk size on an order form for OpenStack resources.
    """
    errors_by_field_id = {}

    rh = env.resource_handler.cast()
    wrapper = rh.get_api_wrapper()
    if rh.resource_technology.name != "OpenStack":
        # This validation only applies to OpenStack
        return errors_by_field_id

    logger.debug(f'validate cfvs: {cfvs}')
    logger.debug(f'validate pcvss: {pcvss}')
    logger.debug(f'validate os_build: {os_build}, type: {type(os_build)}')
    logger.debug(f'validate build_item: {build_item}')
    logger.debug(f'validate env: {env}')

    disk_size = 0
    node_size = None
    cpu_cnt = 0
    mem_size = 0

    for cfv in cfvs:
        if cfv.field.name == "disk_size":
            disk_size = int(cfv.value)
            logger.debug(f'validate disk size: {disk_size} GB')
        if cfv.field.name == "node_size":
            node_size = cfv.value
            logger.debug(f'validate node_size: {node_size}')
        if cfv.field.name == "cpu_cnt":
            cpu_cnt = cfv.value
            logger.debug(f'validate cpu_cnt: {cpu_cnt}')
        if cfv.field.name == "mem_size":
            mem_size = cfv.value
            logger.debug(f'validate mem_size: {mem_size} GB')

    if not os_build or not node_size:
        # When validating on the rate field the error message shows at the top
        # Of the form rather than under the field
        errors_by_field_id["rate"] = (
            f"Unable to validate request, missing required information. "
            f"os_build: {os_build}, node_size: {node_size}."
        )
        return errors_by_field_id
    osba = os_build.osba_for_resource_handler(rh)
    image = wrapper.connection.image.get_image(osba.openstackimage.external_id)
    flavor = wrapper.get_size_object(node_size)

    # Validate disk size against minimum required by OS build
    min_disk = get_min_disk(flavor, image)
    if disk_size < min_disk:
        if disk_size:
            errors_by_field_id["disk_size"] = (
                f"Disk size {disk_size} GB is less than the minimum required "
                f"size of {min_disk} GB for the selected OS build."
            )
        else:
            # disk_size wasn't added to the form, so attach the error to the
            # os_build field so it shows at the top of the form
            errors_by_field_id["os_build"] = (
                f"Disk size {disk_size} GB is less than the minimum required "
                f"size of {min_disk} GB for the selected OS build."
            )

    # Check CPU and Mem Size Present if node size is hotplug
    if wrapper.get_flavor_hotplug(flavor):
        if not cpu_cnt or not mem_size:
            errors_by_field_id["node_size"] = (
                f"Unable to validate compute requirements for hotplug flavor: "
                f"{flavor.name} missing cpu_cnt or mem_size."
            )
        min_ram = get_min_ram(image)
        if mem_size < min_ram:
            errors_by_field_id["mem_size"] = (
                f"Memory size {mem_size} GB is less than the minimum required "
                f"size of {min_ram} GB for the selected OS build."
            )

    return errors_by_field_id


def get_min_disk(flavor, image):
    import math
    # The minimum size of the image in GB round up
    virtual_size = math.ceil(image.virtual_size / 1024 / 1024 / 1024)
    # The size of any ephemeral disk on the flavor
    ephemeral_size = flavor.disk
    image_min_disk = image.min_disk
    if ephemeral_size:
        # If an ephemeral disk is allowed by the flavor, then CloudBolt should
        # allow a size of 0 (meaning use ephemeral only)
        return 0
    if image_min_disk:
        virtual_size = max(virtual_size, image_min_disk)
    return virtual_size


def get_min_ram(image):
    min_ram = image.min_ram if image.min_ram else 512
    return min_ram