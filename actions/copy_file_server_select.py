from infrastructure.models import Server
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def suggest_options(custom_field, query, **kwargs):
    servers = Server.objects.filter(hostname__istartswith=query)
    logger.info(f'copy_file: servers: {servers}')
    return [(s.id, s.hostname) for s in servers]
    
def get_options_list(*args, **kwargs):
    return None
