#!/usr/bin/env python3
"""
Qwen Agent Tool Harness

This module provides a harness for Qwen (or any OpenAI-compatible chat completions API) 
with system access tools modeled after the Grok Research Tool suite.
It enables Qwen to navigate the file system, read/write files, and search the codebase.
"""

import os
import glob
import json
import subprocess
from typing import List, Dict, Any

def list_directory(path: str) -> str:
    """Lists contents of a specified directory."""
    try:
        if not os.path.isdir(path):
            return f"Error: {path} is not a valid directory."
        
        contents = os.listdir(path)
        return json.dumps({
            "path": path,
            "contents": contents
        })
    except Exception as e:
        return f"Error listing directory: {str(e)}"

def read_file(filepath: str, start_line: int = 1, end_line: int = -1) -> str:
    """Reads a file, optionally by line numbers (1-indexed)."""
    try:
        if not os.path.isfile(filepath):
            return f"Error: File {filepath} does not exist."
            
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        if end_line == -1 or end_line > len(lines):
            end_line = len(lines)
            
        start_idx = max(0, start_line - 1)
        end_idx = end_line
        
        selected_lines = lines[start_idx:end_idx]
        
        # Add line numbers for better context
        result = []
        for i, line in enumerate(selected_lines):
            result.append(f"{start_idx + i + 1}: {line.rstrip()}")
            
        return "\n".join(result)
    except Exception as e:
        return f"Error reading file: {str(e)}"

def write_file(filepath: str, content: str, overwrite: bool = False) -> str:
    """Writes content to a file. Creates directories if needed."""
    try:
        if os.path.exists(filepath) and not overwrite:
            return f"Error: File {filepath} exists. Set overwrite=True to overwrite."
            
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
            
        return f"Successfully wrote to {filepath}"
    except Exception as e:
        return f"Error writing file: {str(e)}"

def search_codebase(query: str, directory: str = ".") -> str:
    """Searches the codebase for a text query using grep."""
    try:
        cmd = ["grep", "-rnw", directory, "-e", query]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 1:
            return "No matches found."
        elif result.returncode != 0:
            return f"Error executing search: {result.stderr}"
            
        # Return first 50 lines to prevent context bloat
        lines = result.stdout.split('\n')
        if len(lines) > 50:
            return '\n'.join(lines[:50]) + f"\n... (and {len(lines)-50} more matches)"
        return result.stdout
    except Exception as e:
        return f"Error searching codebase: {str(e)}"

# =============================================================================
# Qwen / OpenAI Tools Definition 
# =============================================================================

QWEN_SYSTEM_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List all files and folders in a specified directory path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The absolute or relative path to the directory."
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Supports line ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "The path to the file to read."
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "The line number to start reading from (1-indexed). Default is 1."
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "The line number to stop reading at. Default is -1 (end of file)."
                    }
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "The path to the file to write to."
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write into the file."
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Whether to overwrite the file if it already exists."
                    }
                },
                "required": ["filepath", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": "Search the codebase for a text string or regex pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The text pattern to search for."
                    },
                    "directory": {
                        "type": "string",
                        "description": "The directory to search within. Defaults to current directory."
                    }
                },
                "required": ["query"]
            }
        }
    }
]

def execute_tool_call(name: str, arguments: dict) -> str:
    """Dispatcher to execute the tool call."""
    try:
        if name == "list_directory":
            return list_directory(arguments.get("path", "."))
        elif name == "read_file":
            return read_file(
                filepath=arguments.get("filepath", ""),
                start_line=arguments.get("start_line", 1),
                end_line=arguments.get("end_line", -1)
            )
        elif name == "write_file":
            return write_file(
                filepath=arguments.get("filepath", ""),
                content=arguments.get("content", ""),
                overwrite=arguments.get("overwrite", False)
            )
        elif name == "search_codebase":
            return search_codebase(
                query=arguments.get("query", ""),
                directory=arguments.get("directory", ".")
            )
        else:
            return f"Error: Tool {name} not found."
    except Exception as e:
        return f"Tool execution failed: {str(e)}"
