"""Information processing layer: six independently replaceable capabilities."""
from .pipeline import analyze
from .topic_search import run as topic_search
from .multimedia_search import run as multimedia_search
from .forum_collection import run as forum_collection
from .insight_analysis import run as insight_analysis
from .report_generation import run as report_generation
from .graph_query import run as graph_query
