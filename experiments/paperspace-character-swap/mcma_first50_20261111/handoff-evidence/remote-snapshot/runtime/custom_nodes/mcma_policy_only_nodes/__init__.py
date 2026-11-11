from .spatial import NODE_CLASS_MAPPINGS
from .minimax_h3_native_temporal import BFSMiniMaxH3NativeTemporal
from .minimax_h3_reference_binding import BFSMiniMaxH3ReferenceBinding
NODE_CLASS_MAPPINGS.update({"BFSMiniMaxH3NativeTemporal":BFSMiniMaxH3NativeTemporal,"BFSMiniMaxH3ReferenceBinding":BFSMiniMaxH3ReferenceBinding})
assert all(isinstance(k,str) for k in NODE_CLASS_MAPPINGS)
