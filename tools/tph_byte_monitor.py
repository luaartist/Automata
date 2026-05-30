#!/usr/bin/env python3
"""
TPH Byte Monitor — Advanced PyTorch Weight State Auditor
Author: Antigravity

This tool loads, inspects, and compares TPH state dictionary models (.pth files)
generated during Kubernetes training iterations. It detects exactly which parameters,
weights, or biases have shifted, computes their L2 norms / mean absolute errors, 
and syncs these semantic change reports directly into Cognee's knowledge graph.
"""

import os
import sys
import torch
import hashlib
import json
import argparse
from typing import Dict, Any, Tuple

# Ensure we can import cognee from the workspace directory
sys.path.insert(0, "/root/workspace/cognee")
from dotenv import load_dotenv
load_dotenv("/root/workspace/cognee/.env")

try:
    import cognee
    _HAS_COGNEE = True
except ImportError:
    _HAS_COGNEE = False

def compute_tensor_hash(tensor: torch.Tensor) -> str:
    """Compute the SHA256 hash of a tensor's raw byte data."""
    # Move to CPU, convert to contiguous float32 numpy array for consistent hashing
    arr = tensor.detach().cpu().to(torch.float32).numpy()
    return hashlib.sha256(arr.tobytes()).hexdigest()

def inspect_model(model_path: str) -> Dict[str, Any]:
    """Inspect a single .pth model file and return key parameters, hashes, and stats."""
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model path not found: {model_path}")
        
    # Load model on CPU
    state_dict = torch.load(model_path, map_location="cpu")
    
    metadata = {}
    # Extract known metadata keys if they exist
    for key in ["training_history", "model_config", "kubernetes_info", "temporal_weights"]:
        if isinstance(state_dict, dict) and key in state_dict:
            val = state_dict[key]
            if isinstance(val, (dict, list, str, int, float, bool)) or val is None:
                metadata[key] = val
            elif isinstance(val, torch.Tensor):
                metadata[key] = {
                    "shape": list(val.shape),
                    "mean": float(val.mean().item()) if val.numel() > 0 else 0.0,
                    "std": float(val.std().item()) if val.numel() > 1 else 0.0
                }
            else:
                metadata[key] = str(val)

    # Unwrap containers
    inner_state = None
    for wrapper in ["state_dict", "model", "model_state_dict"]:
        if isinstance(state_dict, dict) and wrapper in state_dict:
            inner_state = state_dict[wrapper]
            break
            
    if inner_state is not None:
        state_dict = inner_state
        
    total_params = 0
    param_details = {}
    
    if isinstance(state_dict, dict):
        for name, tensor in state_dict.items():
            if not isinstance(tensor, torch.Tensor):
                continue
            shape = list(tensor.shape)
            numel = tensor.numel()
            total_params += numel
            
            # Calculate statistical summaries
            mean_val = float(tensor.mean().item()) if numel > 0 else 0.0
            std_val = float(tensor.std().item()) if numel > 1 else 0.0
            min_val = float(tensor.min().item()) if numel > 0 else 0.0
            max_val = float(tensor.max().item()) if numel > 0 else 0.0
            tensor_hash = compute_tensor_hash(tensor)
            
            param_details[name] = {
                "shape": shape,
                "numel": numel,
                "mean": mean_val,
                "std": std_val,
                "min": min_val,
                "max": max_val,
                "hash": tensor_hash
            }
        
    return {
        "model_path": model_path,
        "filename": os.path.basename(model_path),
        "file_size_bytes": os.path.getsize(model_path),
        "total_parameters": total_params,
        "metadata": metadata,
        "parameters": param_details
    }

def compare_models(model_a_path: str, model_b_path: str) -> Dict[str, Any]:
    """Compare two .pth model files and calculate granular structural and weight differences."""
    meta_a = inspect_model(model_a_path)
    meta_b = inspect_model(model_b_path)
    
    state_a = torch.load(model_a_path, map_location="cpu")
    state_b = torch.load(model_b_path, map_location="cpu")
    
    # Unwrap state dict containers if wrapped
    for key in ["state_dict", "model", "model_state_dict"]:
        if isinstance(state_a, dict) and key in state_a:
            state_a = state_a[key]
        if isinstance(state_b, dict) and key in state_b:
            state_b = state_b[key]
            
    params_a = set(meta_a["parameters"].keys())
    params_b = set(meta_b["parameters"].keys())
    
    added = sorted(list(params_b - params_a))
    removed = sorted(list(params_a - params_b))
    common = sorted(list(params_a & params_b))
    
    changes = {}
    significant_shifts = []
    
    for name in common:
        info_a = meta_a["parameters"][name]
        info_b = meta_b["parameters"][name]
        
        # Check shape mismatch
        if info_a["shape"] != info_b["shape"]:
            changes[name] = {
                "type": "shape_mismatch",
                "old_shape": info_a["shape"],
                "new_shape": info_b["shape"]
            }
            continue
            
        # Check hash mismatch
        if info_a["hash"] != info_b["hash"]:
            t_a = state_a[name].detach().cpu().to(torch.float32)
            t_b = state_b[name].detach().cpu().to(torch.float32)
            
            # Calculate metrics
            abs_diff = torch.abs(t_a - t_b)
            mean_abs_diff = float(abs_diff.mean().item())
            max_abs_diff = float(abs_diff.max().item())
            
            # Relative shift L2 ratio
            norm_a = torch.norm(t_a).item()
            norm_b = torch.norm(t_b).item()
            l2_dist = torch.norm(t_a - t_b).item()
            relative_shift = (l2_dist / (norm_a + 1e-8)) * 100.0  # percentage
            
            change_report = {
                "type": "weight_drift",
                "mean_absolute_difference": mean_abs_diff,
                "max_absolute_difference": max_abs_diff,
                "l2_distance": l2_dist,
                "relative_shift_percentage": relative_shift,
                "old_stats": {
                    "mean": info_a["mean"],
                    "std": info_a["std"]
                },
                "new_stats": {
                    "mean": info_b["mean"],
                    "std": info_b["std"]
                }
            }
            
            changes[name] = change_report
            
            # Mark shifts greater than 0.5% relative change as significant
            if relative_shift > 0.5:
                significant_shifts.append((name, relative_shift, mean_abs_diff))
                
    # Sort significant shifts by magnitude
    significant_shifts.sort(key=lambda x: x[1], reverse=True)
    
    return {
        "model_a": meta_a["filename"],
        "model_b": meta_b["filename"],
        "added_parameters": added,
        "removed_parameters": removed,
        "common_parameters_count": len(common),
        "modified_parameters_count": len(changes),
        "changes": changes,
        "significant_shifts": [
            {"parameter": name, "relative_shift_percentage": rel, "mean_abs_diff": mad}
            for name, rel, mad in significant_shifts
        ]
    }

async def sync_to_cognee(report: Dict[str, Any]) -> str:
    """Format and submit the byte comparison report to the Cognee knowledge graph."""
    if not _HAS_COGNEE:
        return "Cognee module is not accessible."
        
    summary_markdown = f"""
### TPH State Dictionary Transition Audit
* **Base Model**: `{report['model_a']}`
* **Updated Model**: `{report['model_b']}`
* **Structural Details**:
  * Shared parameters: {report['common_parameters_count']}
  * Added parameters: {len(report['added_parameters'])} {report['added_parameters']}
  * Removed parameters: {len(report['removed_parameters'])} {report['removed_parameters']}
  * Modified parameter count: {report['modified_parameters_count']}

### Significant Tensor Weight Drifts (>0.5% relative shift):
"""
    if report["significant_shifts"]:
        for shift in report["significant_shifts"][:10]:  # limit to top 10 for indexing clarity
            summary_markdown += f"- Parameter `{shift['parameter']}` shifted by **{shift['relative_shift_percentage']:.4f}%** (L1 Delta: {shift['mean_abs_diff']:.6f})\n"
    else:
        summary_markdown += "*No parameters experienced significant drift (weights are stable).* \n"
        
    try:
        await cognee.remember(summary_markdown)
        return "Successfully indexed weight changes in Cognee graph database."
    except Exception as e:
        return f"Failed to remember report in Cognee: {str(e)}"

def main():
    parser = argparse.ArgumentParser(description="TPH State dictionary byte difference auditor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Inspect
    inspect_parser = subparsers.add_parser("inspect", help="Inspect structural parameters and SHA256 of single file")
    inspect_parser.add_argument("model_path", type=str, help="Path to the .pth file")
    
    # Compare
    compare_parser = subparsers.add_parser("compare", help="Granular compare two .pth files")
    compare_parser.add_argument("model_a", type=str, help="First model (older base)")
    compare_parser.add_argument("model_b", type=str, help="Second model (newer updated)")
    compare_parser.add_argument("--sync", action="store_true", help="Sync results to Cognee graph")
    
    args = parser.parse_args()
    
    if args.command == "inspect":
        try:
            meta = inspect_model(args.model_path)
            print(json.dumps(meta, indent=2))
        except Exception as e:
            print(json.dumps({"status": "error", "error": str(e)}))
            sys.exit(1)
            
    elif args.command == "compare":
        try:
            comparison = compare_models(args.model_a, args.model_b)
            if args.sync:
                import asyncio
                sync_msg = asyncio.run(sync_to_cognee(comparison))
                comparison["cognee_sync_status"] = sync_msg
            print(json.dumps(comparison, indent=2))
        except Exception as e:
            print(json.dumps({"status": "error", "error": str(e)}))
            sys.exit(1)

if __name__ == "__main__":
    main()
