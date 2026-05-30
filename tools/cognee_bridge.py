#!/usr/bin/env python3
import asyncio
import os
import sys
import json
import argparse
from dotenv import load_dotenv

# Ensure we can import cognee from the workspace directory
sys.path.insert(0, "/root/workspace/cognee")

# Load environment configuration from cognee directory
load_dotenv("/root/workspace/cognee/.env")

import cognee

async def remember_text(text: str):
    try:
        await cognee.remember(text)
        return {"status": "success", "message": f"Successfully remembered: {text[:50]}..."}
    except Exception as e:
        return {"status": "error", "error": str(e)}

async def recall_query(query: str):
    try:
        results = await cognee.recall(query)
        serialized_results = []
        for r in results:
            # Handle list or object results safely
            serialized_results.append({
                "source": getattr(r, "source", "unknown"),
                "text": getattr(r, "text", str(r)),
                "score": getattr(r, "score", None),
                "kind": getattr(r, "kind", "graph")
            })
        return {"status": "success", "results": serialized_results}
    except Exception as e:
        return {"status": "error", "error": str(e)}

def main():
    parser = argparse.ArgumentParser(description="Cognee Local Knowledge Bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Remember command
    remember_parser = subparsers.add_parser("remember", help="Save text to knowledge graph")
    remember_parser.add_argument("text", type=str, help="Text context to save")

    # Recall command
    recall_parser = subparsers.add_parser("recall", help="Recall text from knowledge graph")
    recall_parser.add_argument("query", type=str, help="Search query")

    args = parser.parse_args()

    if args.command == "remember":
        result = asyncio.run(remember_text(args.text))
        print(json.dumps(result))
    elif args.command == "recall":
        result = asyncio.run(recall_query(args.query))
        print(json.dumps(result))

if __name__ == "__main__":
    main()
