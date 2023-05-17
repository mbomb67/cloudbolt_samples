from xui.nsxt.config import run_config
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

__version__ = "1.0"
__credits__ = 'Cloudbolt Software, Inc.'

# explicit list of extensions to be included instead of
# the default of .py and .html only
ALLOWED_XUI_EXTENSIONS = [".py", ".pyc", ".html", ".png", ".js", ".json",
                          ".css", ".jpg", "md"]

run_config(__version__)
