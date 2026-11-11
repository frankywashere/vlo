"""Dense BF16 attention with bounded query output and dead-input-buffer reuse.

No keys/values are removed and no attention precision is reduced. Only the
inference-only H3 path with >100k packed tokens uses this implementation.
"""
import torch
import comfy.ops
import comfy.model_management
import comfy.quant_ops

def install(minimax,threshold=100000,query_chunk=4096,projection_chunk=2048):
 original=minimax.Attention.forward
 seen=set()
 def project(linear,x):
  result=None
  for a in range(0,x.shape[0],projection_chunk):
   b=min(a+projection_chunk,x.shape[0]);part=linear(x[a:b])
   if result is None:result=torch.empty((x.shape[0],part.shape[1]),device=part.device,dtype=part.dtype)
   result[a:b].copy_(part);del part
  return result
 def forward(self,x,rope_freqs=None,transformer_options={}):
  if x.shape[0]<=threshold:return original(self,x,rope_freqs=rope_freqs,transformer_options=transformer_options)
  assert x.ndim==2 and not torch.is_grad_enabled() and x.dtype==torch.bfloat16
  n=x.shape[0];inner=self.heads*self.head_dim
  signature=(n,x.shape[1],self.heads,self.head_dim)
  if signature not in seen:
   seen.add(signature);print('[PRETTY_DENSE_MEMORY_REUSE] '+repr(signature)+f' q_chunk={query_chunk} projection_chunk={projection_chunk}',flush=True)
  qkv_forward=self.qkv_proj.forward;out_forward=self.out_proj.forward
  def out_into_input(z):
   for a in range(0,n,projection_chunk):
    b=min(a+projection_chunk,n);part=out_forward(z[a:b])
    assert part.dtype==x.dtype and part.shape==x[a:b].shape
    x[a:b].copy_(part);del part
   return x
  # Preserve the existing once-per-projection module hook behavior.
  self.qkv_proj.forward=lambda z:project(qkv_forward,z)
  self.out_proj.forward=out_into_input
  try:
   qkv=self.qkv_proj(x);q,k,v=qkv.split(inner,dim=-1);del qkv
   v=v.view(1,n,self.heads,self.head_dim).transpose(1,2)
   if rope_freqs is not None:
    q=q.view(1,n,self.heads,self.head_dim);k=k.view(1,n,self.heads,self.head_dim)
    qw=comfy.model_management.cast_to(self.q_norm.weight,device=x.device)
    kw=comfy.model_management.cast_to(self.k_norm.weight,device=x.device)
    comfy.quant_ops.ck.rms_rope_split_half_(q,k,rope_freqs,qw,kw,epsilon=self.q_norm.eps,rot_dim=rope_freqs.shape[-3]*2)
    q=q.transpose(1,2);k=k.transpose(1,2)
   else:
    q=self.q_norm(q.view(n,self.heads,self.head_dim)).transpose(0,1).unsqueeze(0)
    k=self.k_norm(k.view(n,self.heads,self.head_dim)).transpose(0,1).unsqueeze(0)
   # Q and K/V occupy disjoint channels of their shared packed buffer. Each
   # query chunk is fully consumed before its own Q storage becomes output.
   for a in range(0,n,query_chunk):
    b=min(a+query_chunk,n)
    part=comfy.ops.scaled_dot_product_attention(q[:,:,a:b,:],k,v,attn_mask=None,dropout_p=0.0,is_causal=False)
    q[:,:,a:b,:].copy_(part);del part
   del k,v
   flat=q.transpose(1,2).reshape(1,n,inner)[0]
   assert flat.untyped_storage().data_ptr()==q.untyped_storage().data_ptr(),'Flattening unexpectedly allocated a full output'
   # The caller's normalized attention input is dead after QKV projection.
   # Stream output projection into that buffer instead of a second full tensor.
   return self.out_proj(flat)
  finally:
   self.qkv_proj.forward=qkv_forward;self.out_proj.forward=out_forward
 minimax.Attention.forward=forward
 return original
