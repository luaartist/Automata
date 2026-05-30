"""Wolfram Symbolic Verifier Utility for SU(3) Gell-Mann algebra."""
import os
import sys
import json
import time
import subprocess
from pathlib import Path
import psycopg

# Add parent directory to path so imports work correctly
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import settings

def run_wolfram_code(code: str) -> str:
    """Execute a snippet of Wolfram code via wolframscript and return stdout."""
    try:
        res = subprocess.run(
            ["/usr/bin/wolframscript", "-code", code],
            capture_output=True,
            text=True,
            check=True
        )
        return res.stdout.strip()
    except subprocess.CalledProcessError as exc:
        print(f"wolframscript execution failed: {exc.stderr}")
        raise

def main():
    database_url = settings.DATABASE_URL or os.getenv("DATABASE_URL", "postgresql://appuser:apppassword@localhost:5432/lean4_automata")
    print(f"Connecting to database to fetch YangMills.SU3 companion map...")
    
    # 1. Fetch the companion map info from database
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, file_id, module_name, wolfram_path, file_hash 
                FROM wolfram_companion_maps 
                WHERE module_name = 'YangMills.SU3'
            """)
            row = cur.fetchone()
            if not row:
                print("Error: YangMills.SU3 companion map not found in database.")
                sys.exit(1)
            map_id, file_id, module_name, wolfram_path, file_hash = row
            print(f"Found companion map: ID={map_id}, file_id={file_id}, path={wolfram_path}")

    # Determine absolute path to the .wl file
    wl_path = Path("/root/workspace/Automata/wolfram_lean_maps/YangMills.SU3.wl")
    if not wl_path.exists():
        print(f"Error: Companion map file does not exist at {wl_path}")
        sys.exit(1)

    start_time = time.time()
    
    # 2. Run Verifications
    print("Executing Gell-Mann Matrix Trace Zero verification...")
    trace_code = f'Get["{wl_path}"]; Table[Tr[YangMills`SU3`GellMannMatrix[i]], {{i, 1, 8}}]'
    traces_out = run_wolfram_code(trace_code)
    traces_ok = traces_out == "{0, 0, 0, 0, 0, 0, 0, 0}"
    print(f"Traces: {traces_out} (Valid: {traces_ok})")

    print("Executing Commutation Relations $[\\lambda_a, \\lambda_b] = 2i f_{abc} \\lambda_c$ verification...")
    comm_code = f'Get["{wl_path}"]; Table[YangMills`SU3`GellMannMatrix[a] . YangMills`SU3`GellMannMatrix[b] - YangMills`SU3`GellMannMatrix[b] . YangMills`SU3`GellMannMatrix[a] == 2 * I * Sum[YangMills`SU3`SU3StructureConstant[a, b, c] * YangMills`SU3`GellMannMatrix[c], {{c, 1, 8}}], {{a, 1, 8}}, {{b, 1, 8}}] // FullSimplify'
    comm_out = run_wolfram_code(comm_code)
    comm_ok = "True" in comm_out and all(x.strip() == "True" for x in comm_out.replace("{", "").replace("}", "").split(","))
    print(f"Commutation Relations Valid: {comm_ok}")

    print("Executing Jacobi Identity for Structure Constants verification...")
    jacobi_code = f'Get["{wl_path}"]; Table[Sum[YangMills`SU3`SU3StructureConstant[a, b, d] * YangMills`SU3`SU3StructureConstant[c, d, e] + YangMills`SU3`SU3StructureConstant[b, c, d] * YangMills`SU3`SU3StructureConstant[a, d, e] + YangMills`SU3`SU3StructureConstant[c, a, d] * YangMills`SU3`SU3StructureConstant[b, d, e], {{d, 1, 8}}] == 0, {{a, 1, 8}}, {{b, 1, 8}}, {{c, 1, 8}}, {{e, 1, 8}}] // AllTrue[#, TrueQ, 4]&'
    jacobi_out = run_wolfram_code(jacobi_code)
    jacobi_ok = jacobi_out == "True"
    print(f"Jacobi Identity Valid: {jacobi_ok}")

    duration_ms = int((time.time() - start_time) * 1000)
    all_success = traces_ok and comm_ok and jacobi_ok
    status = "success" if all_success else "error"
    ticket_id = f"WLF-SU3-{int(time.time())}"

    # 3. Write audit ticket to database
    input_json = {
        "wolfram_path": str(wl_path),
        "module_name": module_name,
        "file_hash": file_hash
    }
    output_json = {
        "traces_ok": traces_ok,
        "traces_output": traces_out,
        "commutation_relations_ok": comm_ok,
        "jacobi_identity_ok": jacobi_ok,
        "duration_ms": duration_ms
    }

    print(f"Creating Audit Ticket {ticket_id} in database...")
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO audit_tickets 
                (ticket_id, project_id, flow_name, ticket_type, title, description, input_json, output_json, status, duration_ms, tags, metadata, created_at, completed_at)
                VALUES (%s, 1, 'SU3_Symbolic_Verification', 'wolfram_audit', %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb, now(), now())
            """, (
                ticket_id,
                f"SU(3) Symbolic Verification via WolframScript",
                f"Automated algebraic check of SU(3) Gell-Mann matrices and structure constants from {module_name}.",
                json.dumps(input_json),
                json.dumps(output_json),
                status,
                duration_ms,
                json.dumps(["wolfram", "su3", "yang_mills"]),
                json.dumps({"verified_properties": ["trace_zero", "commutators", "jacobi_identity"]})
            ))
            
            # Update proof obligation status to resolved if successful
            if all_success:
                cur.execute("""
                    UPDATE proof_obligations 
                    SET status = 'resolved', resolved_at = now(), ticket_id = %s
                    WHERE file_id = %s AND obligation_type = 'missing_wolfram_map'
                """, (ticket_id, file_id))
            
            conn.commit()
    print("Audit ticket and proof obligations updated in database successfully!")

    # 4. Generate beautiful Markdown report using a raw string template with double braces
    report_path = Path("/root/workspace/Automata/wolfram_lean_maps/audit_report.md")
    template = r"""# Wolfram Symbolic Verification Audit Report

**Module:** {module_name}
**File ID:** {file_id}
**Companion Map File:** {wl_path}
**Companion Map SHA256:** {file_hash}
**Verification Ticket ID:** {ticket_id}
**Status:** {status_text}
**Execution Duration:** {duration_ms} ms

## Verified Properties

### 1. Gell-Mann Matrix Trace Zero Theorem
- **Lean Theorem:** `SU3.gellMannMatrix_trace_zero`
- **Wolfram Verification:** $\text{{Tr}}(\lambda_i) = 0$ for all $i \in {{1, \dots, 8}}$
- **Result:** {traces_result}
- **Wolfram Output:** {traces_out}

### 2. SU(3) Commutation Relations
- **Lean Definition/Theorem:** `SU3.gellMannMatrix` and `SU3.su3StructureConstant`
- **Mathematical Form:** $[\lambda_a, \lambda_b] = 2i \sum_{{c}} f_{{abc}} \lambda_c$
- **Result:** {comm_result}

### 3. Jacobi Identity for Structure Constants
- **Lean Theorem:** Jacobi algebraic check on structure constants
- **Mathematical Form:** $\sum_d (f_{{abd}} f_{{cde}} + f_{{bcd}} f_{{ade}} + f_{{cad}} f_{{bde}}) = 0$
- **Result:** {jacobi_result}

---
*Generated by Automata Wolfram Symbolic Verifier on {timestamp_utc}.*
"""
    markdown_content = template.format(
        module_name=module_name,
        file_id=file_id,
        wl_path=str(wl_path),
        file_hash=file_hash,
        ticket_id=ticket_id,
        status_text="SUCCESS" if all_success else "FAILED",
        duration_ms=duration_ms,
        traces_result="PASSED" if traces_ok else "FAILED",
        traces_out=traces_out,
        comm_result="PASSED" if comm_ok else "FAILED",
        jacobi_result="PASSED" if jacobi_ok else "FAILED",
        timestamp_utc=time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
    )
    report_path.write_text(markdown_content, encoding="utf-8")
    print(f"Beautiful markdown audit report generated at: {report_path}")

if __name__ == "__main__":
    main()
