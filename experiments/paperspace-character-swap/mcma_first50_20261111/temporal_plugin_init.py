from .spatial import NODE_CLASS_MAPPINGS
from .minimax_h3_native_temporal import BFSMiniMaxH3NativeTemporal
NODE_CLASS_MAPPINGS["BFSMiniMaxH3NativeTemporal"] = BFSMiniMaxH3NativeTemporal
assert all(isinstance(key,str) for key in NODE_CLASS_MAPPINGS)
