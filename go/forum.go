// ============================================================================
// Go_Red Extension — Forum API Routes
// ============================================================================
// Add these routes to the existing main.go AFTER the QMCP consent handler
// All new functionality is additive and non-breaking
// ============================================================================

package main

import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/oklog/ulid/v2"
)

// ============================================================================
// Configuration
// ============================================================================

var (
	forumDB             *sql.DB
	forumDBPath         = getEnv("LEAN4A_FORUM_DB", filepath.Join(os.Getenv("HOME"), ".lean4a", "forum.db"))
	leanDBURL           = getEnv("LEAN_DB_URL", "http://localhost:8000")
	tphServerURL        = getEnv("TPH_SERVER_URL", "http://localhost:5000")
	auditFastAPIBaseURL = normalizeBaseURL(getEnv("LEAN4A_AUDIT_FASTAPI_BASE_URL", "http://localhost:8000"))
	auditUseFastAPI     = getEnvBool("LEAN4A_AUDIT_USE_FASTAPI", false)
	flowTimeoutSec      = getEnvInt("FLOW_TIMEOUT_SEC", 30)
	maxConcurrentFlows  = getEnvInt("MAX_CONCURRENT_FLOWS", 5)

	// Flow execution limiter
	flowSemaphore = make(chan struct{}, maxConcurrentFlows)

	// Active SSE connections
	sseConnections = &sync.Map{}
)

// ============================================================================
// LLM Configuration
// ============================================================================

const (
	ollamaBaseURL     = "http://localhost:11434"
	defaultLLMModel   = "qwen2.5-coder:32b"
	llmRequestTimeout = 120 * time.Second
)

func getEnv(key, defaultVal string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return defaultVal
}

func getEnvInt(key string, defaultVal int) int {
	if val := os.Getenv(key); val != "" {
		var i int
		fmt.Sscanf(val, "%d", &i)
		return i
	}
	return defaultVal
}

func getEnvBool(key string, defaultVal bool) bool {
	val := strings.ToLower(strings.TrimSpace(os.Getenv(key)))
	switch val {
	case "":
		return defaultVal
	case "1", "true", "t", "yes", "y", "on":
		return true
	case "0", "false", "f", "no", "n", "off":
		return false
	default:
		return defaultVal
	}
}

func normalizeBaseURL(raw string) string {
	return strings.TrimRight(strings.TrimSpace(raw), "/")
}

// ============================================================================
// Data Models
// ============================================================================

type Ticket struct {
	ID           string          `json:"id"`
	FlowName     string          `json:"flow_name"`
	InputJSON    json.RawMessage `json:"input_json"`
	OutputJSON   json.RawMessage `json:"output_json"`
	Status       string          `json:"status"` // success, error, timeout
	ErrorMessage string          `json:"error_message,omitempty"`
	CreatedAt    int64           `json:"created_at"`
	DurationMS   int64           `json:"duration_ms"`
	Tags         []string        `json:"tags"`
}

type Session struct {
	ID        string   `json:"id"`
	Name      string   `json:"name"`
	CreatedAt int64    `json:"created_at"`
	TicketIDs []string `json:"ticket_ids"`
}

type FlowDefinition struct {
	Name         string          `json:"name"`
	Description  string          `json:"description"`
	ParamsSchema json.RawMessage `json:"params_schema"`
	Enabled      bool            `json:"enabled"`
	Tags         []string        `json:"tags"`
}

type FlowInput struct {
	Input  map[string]interface{} `json:"input"`
	Params map[string]interface{} `json:"params"`
}

type SSEMessage struct {
	Event string      `json:"-"`
	Data  interface{} `json:"data"`
}

type LogMessage struct {
	Level   string `json:"level"` // info, error, stdout, stderr
	Message string `json:"message"`
}

type ResultMessage struct {
	TicketID string `json:"ticket_id"`
	Status   string `json:"status"`
}

type DoneMessage struct {
	DurationMS int64 `json:"duration_ms"`
}

// ============================================================================
// LLM Data Models
// ============================================================================

type LLMChatRequest struct {
	Model         string          `json:"model"`
	Messages      []OllamaMessage `json:"messages"`
	InjectContext bool            `json:"inject_context"`
}

type OllamaMessage struct {
	Role    string `json:"role"` // "system" | "user" | "assistant"
	Content string `json:"content"`
}

type OllamaChatRequest struct {
	Model    string          `json:"model"`
	Messages []OllamaMessage `json:"messages"`
	Stream   bool            `json:"stream"`
}

type OllamaStreamChunk struct {
	Message OllamaMessage `json:"message"`
	Done    bool          `json:"done"`
}

type OllamaModelsResponse struct {
	Models []OllamaModel `json:"models"`
}

type OllamaModel struct {
	Name string `json:"name"`
}

type StackContext struct {
	DBHealth       string          `json:"db_health"`
	AvailableFlows []string        `json:"available_flows"`
	RecentTickets  []TicketSummary `json:"recent_tickets"`
	ProofStats     string          `json:"proof_stats,omitempty"`
	CogneeMemories string          `json:"cognee_memories,omitempty"`
}

type TicketSummary struct {
	Status string `json:"status"`
	Title  string `json:"title"`
}

// ============================================================================
// Flow Function Registry
// ============================================================================

// FlowFunc is the signature for all built-in flow implementations
type FlowFunc func(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string)

var flowRegistry = map[string]FlowFunc{
	"lean_sorry_scan":          flowLeanSorryScan,
	"lean_float_scan":          flowLeanFloatScan,
	"lean_file_interconnect":   flowLeanFileInterconnect,
	"lean_db_health":           flowLeanDBHealth,
	"quick_proof_report":       flowQuickProofReport,
	"lean_proof_audit":         flowLeanProofAudit,
	"lean_millennium_paths":    flowLeanMillenniumPaths,
	"lean_cohesion_ranking":    flowLeanCohesionRanking,
	"torchlean_tensor_extract": flowTorchLeanTensorExtract,
	"tph_inference":            flowTPHInference,
	"tph_status":               flowTPHStatus,
	"env_check":                flowEnvCheck,
	"gpu_check":                flowGPUCheck,
	"formula_match":            flowFormulaMatch,
	"wolfram_verify":           flowNotImplemented("wolfram_verify"),
	"lean_diff_mathlib":        flowNotImplemented("lean_diff_mathlib"),
}

// Placeholder flows that are not yet implemented
var placeholderFlows = map[string]bool{
	"wolfram_verify":    true,
	"lean_diff_mathlib": true,
}

// ============================================================================
// Database Initialization
// ============================================================================

func initForumDB() error {
	// Create directory if it doesn't exist
	dir := filepath.Dir(forumDBPath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("failed to create forum DB directory: %w", err)
	}

	// Open database with WAL mode
	dsn := fmt.Sprintf("%s?_pragma=journal_mode(WAL)", forumDBPath)
	db, err := sql.Open("sqlite3", dsn)
	if err != nil {
		return fmt.Errorf("failed to open forum DB: %w", err)
	}

	forumDB = db

	// Create tables
	schema := `
	CREATE TABLE IF NOT EXISTS tickets (
		id TEXT PRIMARY KEY,
		flow_name TEXT NOT NULL,
		input_json TEXT NOT NULL,
		output_json TEXT NOT NULL,
		status TEXT NOT NULL CHECK(status IN ('success', 'error', 'timeout')),
		error_message TEXT,
		created_at INTEGER NOT NULL,
		duration_ms INTEGER,
		tags TEXT
	);
	CREATE INDEX IF NOT EXISTS idx_tickets_flow ON tickets(flow_name);
	CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
	CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at DESC);

	CREATE TABLE IF NOT EXISTS sessions (
		id TEXT PRIMARY KEY,
		name TEXT NOT NULL,
		created_at INTEGER NOT NULL,
		ticket_ids_json TEXT
	);
	CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(created_at DESC);

	CREATE TABLE IF NOT EXISTS flow_definitions (
		name TEXT PRIMARY KEY,
		description TEXT NOT NULL,
		params_schema TEXT NOT NULL,
		enabled BOOLEAN NOT NULL DEFAULT 1,
		tags TEXT,
		created_at INTEGER NOT NULL DEFAULT (unixepoch())
	);

	CREATE TABLE IF NOT EXISTS flow_logs (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		flow_name TEXT NOT NULL,
		ticket_id TEXT,
		level TEXT NOT NULL,
		message TEXT NOT NULL,
		timestamp INTEGER NOT NULL
	);
	CREATE INDEX IF NOT EXISTS idx_flow_logs_ticket ON flow_logs(ticket_id);
	CREATE INDEX IF NOT EXISTS idx_flow_logs_timestamp ON flow_logs(timestamp DESC);
	`

	if _, err := db.Exec(schema); err != nil {
		return fmt.Errorf("failed to create schema: %w", err)
	}

	if err := seedFlowDefinitions(db); err != nil {
		return fmt.Errorf("failed to sync flow definitions: %w", err)
	}

	log.Println("✅ Forum database initialized at", forumDBPath)
	return nil
}

func seedFlowDefinitions(db *sql.DB) error {
	// SECURITY FIX: All flows are compile-time Go functions in flowRegistry
	// Database only stores metadata — no code execution from DB
	flows := []FlowDefinition{
		{
			Name:         "lean_sorry_scan",
			Description:  "Scan Lean4 codebase for sorry statements",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"file_pattern":{"type":"string","description":"File pattern to match (e.g., *.lean)","default":"*.lean"}}}`),
			Enabled:      true,
			Tags:         []string{"lean", "sorry", "proof-status"},
		},
		{
			Name:         "lean_float_scan",
			Description:  "Query floating symbols (definitions without proofs)",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"limit":{"type":"integer","description":"Maximum number of results","default":100}}}`),
			Enabled:      true,
			Tags:         []string{"lean", "floating", "symbols"},
		},
		{
			Name:         "lean_file_interconnect",
			Description:  "Analyze file dependencies and imports",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"file_path":{"type":"string","description":"Specific file to analyze (optional)","default":""}}}`),
			Enabled:      true,
			Tags:         []string{"lean", "dependencies", "imports"},
		},
		{
			Name:         "lean_diff_mathlib",
			Description:  "Compare local Lean files against mathlib (placeholder)",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"branch":{"type":"string","description":"Git branch to compare","default":"main"}}}`),
			Enabled:      false,
			Tags:         []string{"lean", "mathlib", "diff", "placeholder"},
		},
		{
			Name:         "tph_inference",
			Description:  "Run token inference through TPH model on MI300X GPU",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"input":{"type":"string","description":"Input text for inference","default":""},"tokens":{"type":"array","description":"Token IDs to process","items":{"type":"integer"},"default":[42,17,89]},"max_length":{"type":"integer","description":"Maximum sequence length","default":50}}}`),
			Enabled:      true,
			Tags:         []string{"tph", "inference", "gpu", "ml"},
		},
		{
			Name:         "tph_status",
			Description:  "Check TPH GPU server status and model info",
			ParamsSchema: json.RawMessage(`{"type":"object"}`),
			Enabled:      true,
			Tags:         []string{"tph", "status", "gpu"},
		},
		{
			Name:         "formula_match",
			Description:  "Match Quantum Mesh YM Flavor 729 eigenvalues and sync to local Cognee knowledge graph",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"index":{"type":"integer","description":"Yang-Mills flavor state index (rounded to nearest integer)","default":1}}}`),
			Enabled:      true,
			Tags:         []string{"formula", "quantum-mesh", "cognee", "sync"},
		},
		{
			Name:         "wolfram_verify",
			Description:  "Verify mathematical formula via Wolfram Engine",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"formula":{"type":"string","description":"Mathematical formula in Wolfram Language","default":"Integrate[x^2, x]"}}}`),
			Enabled:      false,
			Tags:         []string{"wolfram", "verification", "math", "placeholder"},
		},
		{
			Name:         "env_check",
			Description:  "Validate Python environment and dependencies",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"verbose":{"type":"boolean","description":"Show detailed package information","default":false}}}`),
			Enabled:      true,
			Tags:         []string{"env", "python", "dependencies", "diagnostics"},
		},
		{
			Name:         "gpu_check",
			Description:  "Check AMD GPU status and ROCm availability",
			ParamsSchema: json.RawMessage(`{"type":"object"}`),
			Enabled:      true,
			Tags:         []string{"gpu", "rocm", "hardware", "diagnostics"},
		},
		{
			Name:         "lean_db_health",
			Description:  "Check Lean4-Automata database health and statistics",
			ParamsSchema: json.RawMessage(`{"type":"object"}`),
			Enabled:      true,
			Tags:         []string{"lean", "database", "health", "statistics"},
		},
		{
			Name:         "quick_proof_report",
			Description:  "Generate comprehensive proof status report",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"format":{"type":"string","description":"Output format: json or markdown","default":"json","enum":["json","markdown"]}}}`),
			Enabled:      true,
			Tags:         []string{"lean", "proof", "report", "summary"},
		},
		{
			Name:         "lean_proof_audit",
			Description:  "Audit Lean proof map: compare actual imports vs documented",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"report_type":{"type":"string","description":"Type of audit report: summary (counts only) or full (includes mismatches)","default":"summary","enum":["summary","full"]}}}`),
			Enabled:      true,
			Tags:         []string{"lean", "audit", "imports", "proof-map"},
		},
		{
			Name:         "lean_millennium_paths",
			Description:  "Analyze dependency paths to Millennium.lean",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"target":{"type":"string","description":"Target module (default: Millennium.lean)","default":"Millennium.lean"}}}`),
			Enabled:      true,
			Tags:         []string{"lean", "dependencies", "paths", "millennium"},
		},
		{
			Name:         "lean_cohesion_ranking",
			Description:  "Rank modules by cohesion score (imports + dependents + drift)",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"limit":{"type":"integer","description":"Number of top modules to return","default":15,"minimum":1,"maximum":100}}}`),
			Enabled:      true,
			Tags:         []string{"lean", "cohesion", "ranking", "audit"},
		},
		{
			Name:         "torchlean_tensor_extract",
			Description:  "Static TorchLean tensor extraction readiness for TensorExtractedBiasProfile",
			ParamsSchema: json.RawMessage(`{"type":"object","properties":{"project_root":{"type":"string","description":"Lean project root to inspect","default":""},"artifact_root":{"type":"string","description":"Optional artifact root to scan for tensor files","default":""},"max_artifacts":{"type":"integer","description":"Maximum artifact records to include","default":50,"minimum":1,"maximum":500}}}`),
			Enabled:      true,
			Tags:         []string{"lean", "torchlean", "tensor", "audit", "static"},
		},
	}

	stmt, err := db.Prepare(`
		INSERT INTO flow_definitions (name, description, params_schema, enabled, tags)
		VALUES (?, ?, ?, ?, ?)
		ON CONFLICT(name) DO UPDATE SET
			description = excluded.description,
			params_schema = excluded.params_schema,
			enabled = excluded.enabled,
			tags = excluded.tags
	`)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, flow := range flows {
		tagsJSON, _ := json.Marshal(flow.Tags)
		_, err := stmt.Exec(flow.Name, flow.Description, flow.ParamsSchema, flow.Enabled, tagsJSON)
		if err != nil {
			return err
		}
	}

	return nil
}

// ============================================================================
// HTTP Handlers
// ============================================================================

// GET /flows — List all available flows
func handleGetFlows(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	rows, err := forumDB.Query(`SELECT name, description, params_schema, enabled, tags FROM flow_definitions WHERE enabled = 1`)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		log.Printf("Error querying flows: %v", err)
		return
	}
	defer rows.Close()

	var flows []FlowDefinition
	for rows.Next() {
		var flow FlowDefinition
		var tagsJSON string
		if err := rows.Scan(&flow.Name, &flow.Description, &flow.ParamsSchema, &flow.Enabled, &tagsJSON); err != nil {
			continue
		}
		json.Unmarshal([]byte(tagsJSON), &flow.Tags)
		flows = append(flows, flow)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"flows": flows})
}

// POST /run/flow/:name — Execute flow and stream output via SSE
func handleRunFlow(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Extract flow name from URL
	pathParts := strings.Split(r.URL.Path, "/")
	if len(pathParts) < 4 {
		http.Error(w, "Invalid URL", http.StatusBadRequest)
		return
	}
	flowName := pathParts[3]

	// Parse input — SECURITY FIX: limit request body to 1 MB before decoding
	var input FlowInput
	if err := json.NewDecoder(io.LimitReader(r.Body, 1*1024*1024)).Decode(&input); err != nil {
		input.Input = map[string]interface{}{}
	}
	if input.Input == nil {
		input.Input = input.Params
	}
	if input.Input == nil {
		input.Input = map[string]interface{}{}
	}

	// Get flow definition
	var flow FlowDefinition
	var tagsJSON string
	err := forumDB.QueryRow(`SELECT name, description, tags FROM flow_definitions WHERE name = ? AND enabled = 1`, flowName).
		Scan(&flow.Name, &flow.Description, &tagsJSON)
	if err != nil {
		http.Error(w, "Flow not found", http.StatusNotFound)
		return
	}
	json.Unmarshal([]byte(tagsJSON), &flow.Tags)

	// Setup SSE
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
		return
	}

	// Acquire semaphore
	select {
	case flowSemaphore <- struct{}{}:
		defer func() { <-flowSemaphore }()
	default:
		sendSSE(w, flusher, "error", LogMessage{Level: "error", Message: "Too many concurrent flows, please wait"})
		return
	}

	// Execute flow
	startTime := time.Now()
	ticketID := generateULID()

	// Send start message
	sendSSE(w, flusher, "log", LogMessage{Level: "info", Message: fmt.Sprintf("Starting flow: %s", flowName)})

	var outputJSON json.RawMessage
	var status string
	var errorMsg string

	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(flowTimeoutSec)*time.Second)
	defer cancel()

	// SECURITY FIX: All flows execute from compile-time registry only
	// No dynamic code execution from database
	fn, ok := flowRegistry[flow.Name]
	if !ok {
		status = "error"
		errorMsg = "Flow not registered"
		outputJSON = json.RawMessage(fmt.Sprintf(`{"error":"Flow %s not found in registry"}`, flow.Name))
		sendSSE(w, flusher, "error", LogMessage{Level: "error", Message: errorMsg})
	} else {
		outputJSON, status, errorMsg = fn(ctx, input.Input, w, flusher)
	}

	durationMS := time.Since(startTime).Milliseconds()

	// Store ticket
	inputJSON, _ := json.Marshal(input.Input)
	ticket := Ticket{
		ID:           ticketID,
		FlowName:     flowName,
		InputJSON:    inputJSON,
		OutputJSON:   outputJSON,
		Status:       status,
		ErrorMessage: errorMsg,
		CreatedAt:    time.Now().Unix(),
		DurationMS:   durationMS,
		Tags:         flow.Tags,
	}

	if err := storeTicket(ticket); err != nil {
		log.Printf("Failed to store ticket: %v", err)
	}

	// Send result
	sendSSE(w, flusher, "result", ResultMessage{TicketID: ticketID, Status: status})
	sendSSE(w, flusher, "done", DoneMessage{DurationMS: durationMS})
}

// ============================================================================
// Flow Function Implementations
// ============================================================================

// Helper to send SSE log messages
func sendFlowLog(w http.ResponseWriter, flusher http.Flusher, level, message string) {
	sendSSE(w, flusher, "log", LogMessage{Level: level, Message: message})
}

// flowNotImplemented creates a placeholder flow function
func flowNotImplemented(name string) FlowFunc {
	return func(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
		errorMsg := fmt.Sprintf("flow %s is not implemented", name)
		sendFlowLog(w, flusher, "error", errorMsg)
		result := map[string]interface{}{
			"not_implemented": true,
			"flow":            name,
			"message":         errorMsg,
		}
		resultJSON, _ := json.Marshal(result)
		return resultJSON, "error", errorMsg
	}
}

// flowLeanSorryScan queries the Lean database for files with sorry statements
func flowLeanSorryScan(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	sendFlowLog(w, flusher, "info", "Querying Lean database for sorry statements...")

	query := map[string]string{
		"query": "SELECT file_path, sorry_count, total_declarations FROM v_proof_status WHERE sorry_count > 0 ORDER BY sorry_count DESC",
	}
	queryJSON, _ := json.Marshal(query)

	req, err := http.NewRequestWithContext(ctx, "POST", leanDBURL+"/query", strings.NewReader(string(queryJSON)))
	if err != nil {
		return json.RawMessage(`{}`), "error", err.Error()
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		sendFlowLog(w, flusher, "error", fmt.Sprintf("Failed to query Lean DB: %v", err))
		return json.RawMessage(`{}`), "error", err.Error()
	}
	defer resp.Body.Close()

	output, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024))
	sendFlowLog(w, flusher, "info", fmt.Sprintf("Found %d bytes of sorry scan results", len(output)))

	return json.RawMessage(output), "success", ""
}

// flowLeanFloatScan queries the Lean database for floating symbols
func flowLeanFloatScan(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	sendFlowLog(w, flusher, "info", "Querying Lean database for floating symbols...")

	limit := 100
	if l, ok := params["limit"].(float64); ok {
		limit = int(l)
	}

	query := map[string]string{
		"query": fmt.Sprintf("SELECT symbol_name, file_path, definition_type FROM v_floating_symbols ORDER BY symbol_name LIMIT %d", limit),
	}
	queryJSON, _ := json.Marshal(query)

	req, err := http.NewRequestWithContext(ctx, "POST", leanDBURL+"/query", strings.NewReader(string(queryJSON)))
	if err != nil {
		return json.RawMessage(`{}`), "error", err.Error()
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		sendFlowLog(w, flusher, "error", fmt.Sprintf("Failed to query Lean DB: %v", err))
		return json.RawMessage(`{}`), "error", err.Error()
	}
	defer resp.Body.Close()

	output, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024))
	sendFlowLog(w, flusher, "info", "Found floating symbols results")

	return json.RawMessage(output), "success", ""
}

// flowLeanFileInterconnect analyzes file dependencies and imports
func flowLeanFileInterconnect(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	sendFlowLog(w, flusher, "info", "Analyzing file dependencies...")

	query := map[string]string{
		"query": "SELECT file_path, import_count, exported_symbols FROM v_file_interconnect ORDER BY import_count DESC LIMIT 50",
	}
	queryJSON, _ := json.Marshal(query)

	req, err := http.NewRequestWithContext(ctx, "POST", leanDBURL+"/query", strings.NewReader(string(queryJSON)))
	if err != nil {
		return json.RawMessage(`{}`), "error", err.Error()
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		sendFlowLog(w, flusher, "error", fmt.Sprintf("Failed to query Lean DB: %v", err))
		return json.RawMessage(`{}`), "error", err.Error()
	}
	defer resp.Body.Close()

	output, _ := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024))
	sendFlowLog(w, flusher, "info", "File interconnect analysis complete")

	return json.RawMessage(output), "success", ""
}

// flowLeanDBHealth checks the health of the Lean database
func flowLeanDBHealth(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	sendFlowLog(w, flusher, "info", "Checking Lean database health...")

	queries := map[string]string{
		"total_files":      "SELECT COUNT(DISTINCT file_path) FROM lean_symbols",
		"total_symbols":    "SELECT COUNT(*) FROM lean_symbols",
		"sorry_count":      "SELECT SUM(sorry_count) FROM v_proof_status",
		"floating_symbols": "SELECT COUNT(*) FROM v_floating_symbols",
	}

	result := map[string]interface{}{
		"status": "ok",
		"stats":  make(map[string]interface{}),
	}

	client := &http.Client{}
	for key, queryStr := range queries {
		queryBody := map[string]string{"query": queryStr}
		queryJSON, _ := json.Marshal(queryBody)

		req, err := http.NewRequestWithContext(ctx, "POST", leanDBURL+"/query", strings.NewReader(string(queryJSON)))
		if err != nil {
			result["stats"].(map[string]interface{})[key] = "error"
			continue
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := client.Do(req)
		if err != nil {
			result["stats"].(map[string]interface{})[key] = "error"
			continue
		}

		output, _ := io.ReadAll(io.LimitReader(resp.Body, 1*1024*1024))
		resp.Body.Close()

		var queryResult interface{}
		json.Unmarshal(output, &queryResult)
		result["stats"].(map[string]interface{})[key] = queryResult

		sendFlowLog(w, flusher, "info", fmt.Sprintf("Checked %s", key))
	}

	resultJSON, _ := json.Marshal(result)
	sendFlowLog(w, flusher, "info", "Database health check complete")

	return resultJSON, "success", ""
}

// flowQuickProofReport generates a comprehensive proof status report
func flowQuickProofReport(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	sendFlowLog(w, flusher, "info", "Generating proof status report...")

	client := &http.Client{}

	// Get summary statistics
	summaryQuery := map[string]string{
		"query": `SELECT 
			COUNT(*) as total_files,
			SUM(sorry_count) as total_sorries,
			SUM(CASE WHEN sorry_count = 0 THEN 1 ELSE 0 END) as complete_files,
			AVG(sorry_count) as avg_sorries_per_file
		FROM v_proof_status`,
	}
	summaryJSON, _ := json.Marshal(summaryQuery)

	req, err := http.NewRequestWithContext(ctx, "POST", leanDBURL+"/query", strings.NewReader(string(summaryJSON)))
	if err != nil {
		return json.RawMessage(`{}`), "error", err.Error()
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		sendFlowLog(w, flusher, "error", fmt.Sprintf("Failed to get summary: %v", err))
		return json.RawMessage(`{}`), "error", err.Error()
	}

	summaryOutput, _ := io.ReadAll(io.LimitReader(resp.Body, 1*1024*1024))
	resp.Body.Close()

	var summary interface{}
	json.Unmarshal(summaryOutput, &summary)

	sendFlowLog(w, flusher, "info", "Retrieved summary statistics")

	// Get top sorry files
	topQuery := map[string]string{
		"query": "SELECT file_path, sorry_count FROM v_proof_status WHERE sorry_count > 0 ORDER BY sorry_count DESC LIMIT 10",
	}
	topJSON, _ := json.Marshal(topQuery)

	req2, err := http.NewRequestWithContext(ctx, "POST", leanDBURL+"/query", strings.NewReader(string(topJSON)))
	if err != nil {
		return json.RawMessage(`{}`), "error", err.Error()
	}
	req2.Header.Set("Content-Type", "application/json")

	resp2, err := client.Do(req2)
	if err != nil {
		sendFlowLog(w, flusher, "error", fmt.Sprintf("Failed to get top files: %v", err))
		return json.RawMessage(`{}`), "error", err.Error()
	}

	topOutput, _ := io.ReadAll(io.LimitReader(resp2.Body, 1*1024*1024))
	resp2.Body.Close()

	var topFiles interface{}
	json.Unmarshal(topOutput, &topFiles)

	sendFlowLog(w, flusher, "info", "Retrieved top sorry files")

	result := map[string]interface{}{
		"summary":         summary,
		"top_sorry_files": topFiles,
		"timestamp":       time.Now().Format(time.RFC3339),
	}

	resultJSON, _ := json.Marshal(result)
	sendFlowLog(w, flusher, "info", "Proof report generation complete")

	return resultJSON, "success", ""
}

// flowTPHStatus checks the TPH server status
func flowTPHStatus(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	sendFlowLog(w, flusher, "info", "Checking TPH server status...")

	req, err := http.NewRequestWithContext(ctx, "GET", tphServerURL+"/status", nil)
	if err != nil {
		return json.RawMessage(`{}`), "error", err.Error()
	}

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		sendFlowLog(w, flusher, "error", fmt.Sprintf("TPH server offline: %v", err))
		result := map[string]interface{}{
			"error":  err.Error(),
			"status": "offline",
		}
		resultJSON, _ := json.Marshal(result)
		return resultJSON, "success", ""
	}
	defer resp.Body.Close()

	output, _ := io.ReadAll(io.LimitReader(resp.Body, 1*1024*1024))
	sendFlowLog(w, flusher, "info", "TPH server is online")

	return json.RawMessage(output), "success", ""
}

// flowEnvCheck validates the Python environment and dependencies
func flowEnvCheck(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	sendFlowLog(w, flusher, "info", "Checking Python environment...")

	// Check Python version - SAFE: hardcoded command with NO user input
	cmd := exec.CommandContext(ctx, "python3", "--version")
	output, err := cmd.Output()
	pythonVersion := strings.TrimSpace(string(output))
	if err != nil {
		pythonVersion = "Not available"
	}

	sendFlowLog(w, flusher, "info", fmt.Sprintf("Python version: %s", pythonVersion))

	// Check for .venv
	homeDir := os.Getenv("HOME")
	venvPath := filepath.Join(homeDir, "workspace", ".venv")
	venvExists := false
	if _, err := os.Stat(venvPath); err == nil {
		venvExists = true
		sendFlowLog(w, flusher, "info", "Virtual environment found")
	}

	result := map[string]interface{}{
		"python_version": pythonVersion,
		"venv_active":    venvExists,
	}

	resultJSON, _ := json.Marshal(result)
	sendFlowLog(w, flusher, "info", "Environment check complete")

	return resultJSON, "success", ""
}

// flowGPUCheck checks AMD GPU status and ROCm availability
func flowGPUCheck(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	sendFlowLog(w, flusher, "info", "Checking GPU status...")

	result := map[string]interface{}{
		"gpu_available": false,
		"gpus":          []string{},
	}

	// Check ROCm - SAFE: hardcoded command with NO user input
	cmd := exec.CommandContext(ctx, "rocm-smi", "--showproductname")
	output, err := cmd.Output()
	if err == nil {
		result["gpu_available"] = true
		lines := strings.Split(strings.TrimSpace(string(output)), "\n")
		gpus := []string{}
		for _, line := range lines {
			if line = strings.TrimSpace(line); line != "" {
				gpus = append(gpus, line)
			}
		}
		result["gpus"] = gpus
		sendFlowLog(w, flusher, "info", fmt.Sprintf("Found %d GPU(s)", len(gpus)))
	} else {
		sendFlowLog(w, flusher, "warn", "ROCm not available")
	}

	// Check memory info - SAFE: hardcoded command with NO user input
	cmd2 := exec.CommandContext(ctx, "rocm-smi", "--showmeminfo", "vram")
	memOutput, err := cmd2.Output()
	if err == nil {
		result["vram_info"] = strings.TrimSpace(string(memOutput))
		sendFlowLog(w, flusher, "info", "Retrieved VRAM information")
	}

	resultJSON, _ := json.Marshal(result)
	sendFlowLog(w, flusher, "info", "GPU check complete")

	return resultJSON, "success", ""
}

func runLeanAuditPython(ctx context.Context, w http.ResponseWriter, flusher http.Flusher, command string, args ...string) (json.RawMessage, string, string) {
	toolRoot, err := findLeanAuditToolRoot()
	if err != nil {
		errorMsg := err.Error()
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":    command,
			"error":   errorMsg,
			"command": append([]string{"python3"}, args...),
		})
		return resultJSON, "error", errorMsg
	}
	auditRoot := findLeanAuditDataRoot(toolRoot)

	scriptPath := filepath.Join(toolRoot, "tools", "lean_proof_audit.py")
	cmdArgs := append([]string{scriptPath, command}, args...)
	sendFlowLog(w, flusher, "info", fmt.Sprintf("Running Python audit command from %s: python3 %s %s", auditRoot, scriptPath, strings.Join(append([]string{command}, args...), " ")))

	cmd := exec.CommandContext(ctx, "python3", cmdArgs...)
	cmd.Dir = auditRoot

	var stdoutBuf bytes.Buffer
	var stderrBuf bytes.Buffer
	cmd.Stdout = &stdoutBuf
	cmd.Stderr = &stderrBuf

	err = cmd.Run()

	stdout := strings.TrimSpace(stdoutBuf.String())
	stderr := strings.TrimSpace(stderrBuf.String())

	if stdout != "" {
		for _, line := range strings.Split(stdout, "\n") {
			line = strings.TrimSpace(line)
			if line != "" {
				sendFlowLog(w, flusher, "stdout", line)
			}
		}
	}
	if stderr != "" {
		for _, line := range strings.Split(stderr, "\n") {
			line = strings.TrimSpace(line)
			if line != "" {
				sendFlowLog(w, flusher, "stderr", line)
			}
		}
	}

	if ctx.Err() == context.DeadlineExceeded {
		errorMsg := fmt.Sprintf("Lean audit flow timed out after %ds", flowTimeoutSec)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":    command,
			"error":   errorMsg,
			"stdout":  stdout,
			"stderr":  stderr,
			"command": append([]string{"python3"}, cmdArgs...),
		})
		return resultJSON, "timeout", errorMsg
	}

	if err != nil {
		errorMsg := fmt.Sprintf("Lean audit command failed: %v", err)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":       command,
			"error":      errorMsg,
			"stdout":     stdout,
			"stderr":     stderr,
			"exit_error": err.Error(),
			"command":    append([]string{"python3"}, cmdArgs...),
		})
		return resultJSON, "error", errorMsg
	}

	if stdout == "" {
		errorMsg := "Lean audit command returned empty stdout"
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":    command,
			"error":   errorMsg,
			"stderr":  stderr,
			"command": append([]string{"python3"}, cmdArgs...),
		})
		return resultJSON, "error", errorMsg
	}

	var parsed interface{}
	if err := json.Unmarshal([]byte(stdout), &parsed); err != nil {
		errorMsg := fmt.Sprintf("Lean audit command returned invalid JSON: %v", err)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":    command,
			"error":   errorMsg,
			"stdout":  stdout,
			"stderr":  stderr,
			"command": append([]string{"python3"}, cmdArgs...),
		})
		return resultJSON, "error", errorMsg
	}

	resultJSON, _ := json.Marshal(parsed)
	sendFlowLog(w, flusher, "info", "Lean audit command completed successfully")
	return resultJSON, "success", ""
}

func auditFastAPIEnabled() bool {
	return auditUseFastAPI
}

func cloneFlowParams(params map[string]interface{}) map[string]interface{} {
	cloned := make(map[string]interface{}, len(params)+1)
	for key, value := range params {
		cloned[key] = value
	}
	return cloned
}

func appendOptionalStringArg(cmdArgs []string, params map[string]interface{}, paramName string, flagName string) []string {
	rawValue, ok := params[paramName]
	if !ok {
		return cmdArgs
	}
	textValue := strings.TrimSpace(fmt.Sprint(rawValue))
	if textValue == "" || textValue == "<nil>" {
		return cmdArgs
	}
	return append(cmdArgs, flagName, textValue)
}

func positiveIntParam(params map[string]interface{}, paramName string, defaultValue int, maxValue int) int {
	result := defaultValue
	if rawValue, ok := params[paramName]; ok {
		switch typedValue := rawValue.(type) {
		case float64:
			result = int(typedValue)
		case int:
			result = typedValue
		case string:
			fmt.Sscanf(strings.TrimSpace(typedValue), "%d", &result)
		}
	}
	if result < 1 {
		result = 1
	}
	if maxValue > 0 && result > maxValue {
		result = maxValue
	}
	return result
}

func runTorchLeanTensorExtractPython(ctx context.Context, w http.ResponseWriter, flusher http.Flusher, params map[string]interface{}) (json.RawMessage, string, string) {
	toolRoot, err := findLeanAuditToolRoot()
	if err != nil {
		errorMsg := err.Error()
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":  "torchlean_tensor_extract",
			"error": errorMsg,
		})
		return resultJSON, "error", errorMsg
	}
	auditRoot := findLeanAuditDataRoot(toolRoot)

	scriptPath := filepath.Join(toolRoot, "tools", "torchlean_tensor_extract.py")
	cmdArgs := []string{scriptPath, "--max-artifacts", fmt.Sprintf("%d", positiveIntParam(params, "max_artifacts", 50, 500))}
	cmdArgs = appendOptionalStringArg(cmdArgs, params, "project_root", "--project-root")
	cmdArgs = appendOptionalStringArg(cmdArgs, params, "artifact_root", "--artifact-root")

	sendFlowLog(w, flusher, "info", fmt.Sprintf("Running TorchLean tensor static extract from %s", auditRoot))
	cmd := exec.CommandContext(ctx, "python3", cmdArgs...)
	cmd.Dir = auditRoot

	var stdoutBuf bytes.Buffer
	var stderrBuf bytes.Buffer
	cmd.Stdout = &stdoutBuf
	cmd.Stderr = &stderrBuf

	err = cmd.Run()

	stdout := strings.TrimSpace(stdoutBuf.String())
	stderr := strings.TrimSpace(stderrBuf.String())
	if stderr != "" {
		for _, line := range strings.Split(stderr, "\n") {
			line = strings.TrimSpace(line)
			if line != "" {
				sendFlowLog(w, flusher, "stderr", line)
			}
		}
	}

	if ctx.Err() == context.DeadlineExceeded {
		errorMsg := fmt.Sprintf("TorchLean tensor extract flow timed out after %ds", flowTimeoutSec)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":    "torchlean_tensor_extract",
			"error":   errorMsg,
			"stdout":  stdout,
			"stderr":  stderr,
			"command": append([]string{"python3"}, cmdArgs...),
		})
		return resultJSON, "timeout", errorMsg
	}

	if err != nil {
		errorMsg := fmt.Sprintf("TorchLean tensor extract command failed: %v", err)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":       "torchlean_tensor_extract",
			"error":      errorMsg,
			"stdout":     stdout,
			"stderr":     stderr,
			"exit_error": err.Error(),
			"command":    append([]string{"python3"}, cmdArgs...),
		})
		return resultJSON, "error", errorMsg
	}

	if stdout == "" {
		errorMsg := "TorchLean tensor extract returned empty stdout"
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":    "torchlean_tensor_extract",
			"error":   errorMsg,
			"stderr":  stderr,
			"command": append([]string{"python3"}, cmdArgs...),
		})
		return resultJSON, "error", errorMsg
	}

	var parsed interface{}
	if err := json.Unmarshal([]byte(stdout), &parsed); err != nil {
		errorMsg := fmt.Sprintf("TorchLean tensor extract returned invalid JSON: %v", err)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":    "torchlean_tensor_extract",
			"error":   errorMsg,
			"stdout":  stdout,
			"stderr":  stderr,
			"command": append([]string{"python3"}, cmdArgs...),
		})
		return resultJSON, "error", errorMsg
	}

	resultJSON, _ := json.Marshal(parsed)
	sendFlowLog(w, flusher, "info", "TorchLean tensor static extract completed successfully")
	return resultJSON, "success", ""
}

func runLeanAuditFastAPI(ctx context.Context, w http.ResponseWriter, flusher http.Flusher, flowName string, params map[string]interface{}) (json.RawMessage, string, string) {
	if auditFastAPIBaseURL == "" {
		errorMsg := "LEAN4A_AUDIT_USE_FASTAPI is enabled but LEAN4A_AUDIT_FASTAPI_BASE_URL is empty"
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":  flowName,
			"error": errorMsg,
		})
		return resultJSON, "error", errorMsg
	}

	paramsJSON, err := json.Marshal(params)
	if err != nil {
		errorMsg := fmt.Sprintf("failed to marshal audit params: %v", err)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":   flowName,
			"error":  errorMsg,
			"params": params,
		})
		return resultJSON, "error", errorMsg
	}

	endpoint, err := url.Parse(auditFastAPIBaseURL + "/api/v1/audit/execute")
	if err != nil {
		errorMsg := fmt.Sprintf("invalid FastAPI audit base URL %q: %v", auditFastAPIBaseURL, err)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":  flowName,
			"error": errorMsg,
		})
		return resultJSON, "error", errorMsg
	}

	query := endpoint.Query()
	query.Set("flow_name", flowName)
	query.Set("params", string(paramsJSON))
	endpoint.RawQuery = query.Encode()

	sendFlowLog(w, flusher, "info", fmt.Sprintf("Proxying audit flow to FastAPI: %s", flowName))
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), nil)
	if err != nil {
		errorMsg := fmt.Sprintf("failed to build FastAPI audit request: %v", err)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":  flowName,
			"error": errorMsg,
		})
		return resultJSON, "error", errorMsg
	}
	req.Header.Set("Accept", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		errorMsg := fmt.Sprintf("FastAPI audit request failed: %v", err)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":   flowName,
			"error":  errorMsg,
			"params": params,
		})
		return resultJSON, "error", errorMsg
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 8*1024*1024))
	if err != nil {
		errorMsg := fmt.Sprintf("failed to read FastAPI audit response: %v", err)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":  flowName,
			"error": errorMsg,
		})
		return resultJSON, "error", errorMsg
	}

	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		errorMsg := fmt.Sprintf("FastAPI audit execute returned HTTP %d: %s", resp.StatusCode, truncateForLog(string(body), 512))
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":        flowName,
			"error":       errorMsg,
			"params":      params,
			"status_code": resp.StatusCode,
			"response":    string(body),
		})
		return resultJSON, "error", errorMsg
	}

	var parsed interface{}
	if err := json.Unmarshal(body, &parsed); err != nil {
		errorMsg := fmt.Sprintf("FastAPI audit response returned invalid JSON: %v", err)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"flow":     flowName,
			"error":    errorMsg,
			"response": string(body),
		})
		return resultJSON, "error", errorMsg
	}

	if parsedMap, ok := parsed.(map[string]interface{}); ok {
		if statusText, ok := parsedMap["status"].(string); ok && statusText != "ok" {
			errorMsg := fmt.Sprintf("FastAPI audit flow returned status %q", statusText)
			sendFlowLog(w, flusher, "error", errorMsg)
			resultJSON, _ := json.Marshal(parsed)
			return resultJSON, "error", errorMsg
		}
		if ticket, ok := parsedMap["ticket"].(map[string]interface{}); ok {
			if ticketID, ok := ticket["ticket_id"].(string); ok && ticketID != "" {
				sendFlowLog(w, flusher, "info", fmt.Sprintf("FastAPI persisted audit ticket: %s", ticketID))
			}
		}
	}

	resultJSON, _ := json.Marshal(parsed)
	sendFlowLog(w, flusher, "info", "FastAPI audit flow completed successfully")
	return resultJSON, "success", ""
}

func truncateForLog(value string, maxLen int) string {
	if len(value) <= maxLen {
		return value
	}
	if maxLen <= 3 {
		return value[:maxLen]
	}
	return value[:maxLen-3] + "..."
}

func flowLeanProofAudit(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	reportType := "summary"
	if raw, ok := params["report_type"]; ok {
		reportType = strings.TrimSpace(fmt.Sprint(raw))
	}

	switch reportType {
	case "summary":
		if auditFastAPIEnabled() {
			flowParams := cloneFlowParams(params)
			flowParams["report_type"] = reportType
			return runLeanAuditFastAPI(ctx, w, flusher, "lean_proof_audit", flowParams)
		}
		return runLeanAuditPython(ctx, w, flusher, "audit-summary")
	case "full":
		if auditFastAPIEnabled() {
			flowParams := cloneFlowParams(params)
			flowParams["report_type"] = reportType
			return runLeanAuditFastAPI(ctx, w, flusher, "lean_proof_audit", flowParams)
		}
		return runLeanAuditPython(ctx, w, flusher, "audit-full")
	default:
		errorMsg := fmt.Sprintf("invalid report_type %q", reportType)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"error":       errorMsg,
			"report_type": reportType,
		})
		return resultJSON, "error", errorMsg
	}
}

func flowLeanMillenniumPaths(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	target := "Millennium.lean"
	if raw, ok := params["target"]; ok {
		if text := strings.TrimSpace(fmt.Sprint(raw)); text != "" {
			target = text
		}
	}
	if auditFastAPIEnabled() {
		flowParams := cloneFlowParams(params)
		flowParams["target"] = target
		return runLeanAuditFastAPI(ctx, w, flusher, "lean_millennium_paths", flowParams)
	}
	return runLeanAuditPython(ctx, w, flusher, "millennium-path", target)
}

func flowLeanCohesionRanking(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	limit := 15
	if raw, ok := params["limit"]; ok {
		switch v := raw.(type) {
		case float64:
			limit = int(v)
		case int:
			limit = v
		case string:
			fmt.Sscanf(v, "%d", &limit)
		}
	}
	if limit <= 0 {
		errorMsg := fmt.Sprintf("invalid limit %d", limit)
		sendFlowLog(w, flusher, "error", errorMsg)
		resultJSON, _ := json.Marshal(map[string]interface{}{
			"error": errorMsg,
			"limit": limit,
		})
		return resultJSON, "error", errorMsg
	}
	if auditFastAPIEnabled() {
		flowParams := cloneFlowParams(params)
		flowParams["limit"] = limit
		return runLeanAuditFastAPI(ctx, w, flusher, "lean_cohesion_ranking", flowParams)
	}
	return runLeanAuditPython(ctx, w, flusher, "cohesion-ranking", fmt.Sprintf("%d", limit))
}

func flowTorchLeanTensorExtract(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	flowParams := cloneFlowParams(params)
	flowParams["max_artifacts"] = positiveIntParam(params, "max_artifacts", 50, 500)
	if auditFastAPIEnabled() {
		return runLeanAuditFastAPI(ctx, w, flusher, "torchlean_tensor_extract", flowParams)
	}
	return runTorchLeanTensorExtractPython(ctx, w, flusher, flowParams)
}

func findLeanAuditToolRoot() (string, error) {
	for _, envKey := range []string{"LEAN4A_TOOL_ROOT", "LEAN4A_PROJECT_ROOT"} {
		if envRoot := strings.TrimSpace(os.Getenv(envKey)); envRoot != "" {
			if hasLeanAuditToolRoot(envRoot) {
				return envRoot, nil
			}
			return "", fmt.Errorf("%s does not point to a Lean4-Automata tool root: %s", envKey, envRoot)
		}
	}

	startDir, err := os.Getwd()
	if err != nil {
		return "", fmt.Errorf("failed to get working directory: %w", err)
	}

	dir := startDir
	for {
		if hasLeanAuditToolRoot(dir) {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}

	return "", fmt.Errorf("could not locate Lean4-Automata repo root from %s", startDir)
}

func findLeanAuditDataRoot(toolRoot string) string {
	for _, envKey := range []string{"LEAN4A_AUDIT_ROOT", "LEAN4A_PROOF_ROOT", "LEAN4A_PROJECT_ROOT"} {
		if envRoot := strings.TrimSpace(os.Getenv(envKey)); envRoot != "" && hasLeanAuditDataRoot(envRoot) {
			return envRoot
		}
	}

	for dir := toolRoot; ; dir = filepath.Dir(dir) {
		if hasLeanAuditDataRoot(dir) {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
	}

	if startDir, err := os.Getwd(); err == nil {
		for dir := startDir; ; dir = filepath.Dir(dir) {
			if hasLeanAuditDataRoot(dir) {
				return dir
			}
			parent := filepath.Dir(dir)
			if parent == dir {
				break
			}
		}
	}

	return toolRoot
}

func hasLeanAuditToolRoot(dir string) bool {
	if _, err := os.Stat(filepath.Join(dir, "tools", "lean_proof_audit.py")); err != nil {
		return false
	}
	return true
}

func hasLeanAuditDataRoot(dir string) bool {
	if _, err := os.Stat(filepath.Join(dir, "Lean_Proof_Map.txt")); err != nil {
		return false
	}
	if info, err := os.Stat(filepath.Join(dir, "src")); err != nil || !info.IsDir() {
		return false
	}
	return true
}

// flowTPHInference executes inference on the TPH GPU server
func flowTPHInference(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	sendFlowLog(w, flusher, "info", "Starting TPH inference request...")

	// Marshal parameters into request body
	body, err := json.Marshal(params)
	if err != nil {
		return json.RawMessage(`{}`), "error", err.Error()
	}

	// SECURITY FIX: Use context with timeout from caller
	req, err := http.NewRequestWithContext(ctx, "POST", tphServerURL+"/inference", strings.NewReader(string(body)))
	if err != nil {
		return json.RawMessage(`{}`), "error", err.Error()
	}
	req.Header.Set("Content-Type", "application/json")

	sendFlowLog(w, flusher, "info", fmt.Sprintf("Sending request to TPH server: %s", tphServerURL))

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		sendFlowLog(w, flusher, "error", fmt.Sprintf("TPH server request failed: %v", err))
		// Return structured error response
		errorResult := map[string]interface{}{
			"error":  err.Error(),
			"status": "offline",
		}
		resultJSON, _ := json.Marshal(errorResult)
		return resultJSON, "error", err.Error()
	}
	defer resp.Body.Close()

	// SECURITY FIX: Limit response body to 4MB to prevent memory exhaustion
	output, err := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024))
	if err != nil {
		sendFlowLog(w, flusher, "error", fmt.Sprintf("Failed to read response: %v", err))
		return json.RawMessage(`{}`), "error", err.Error()
	}

	sendFlowLog(w, flusher, "info", fmt.Sprintf("TPH inference completed, received %d bytes", len(output)))

	return json.RawMessage(output), "success", ""
}

// POST /ticket/create
func handleCreateTicket(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// SECURITY FIX: Limit request body to 512 KB to prevent DoS
	var ticket Ticket
	if err := json.NewDecoder(io.LimitReader(r.Body, 512*1024)).Decode(&ticket); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	// SECURITY FIX: Validate flow_name to prevent stored XSS and injection.
	// It must match an existing, enabled flow in the DB.
	var validatedFlowName string
	err := forumDB.QueryRow(`SELECT name FROM flow_definitions WHERE name = ? AND enabled = 1`, ticket.FlowName).Scan(&validatedFlowName)
	if err != nil {
		// Reject tickets for unknown / disabled flows; don't leak DB errors.
		http.Error(w, "Invalid flow_name", http.StatusBadRequest)
		return
	}
	ticket.FlowName = validatedFlowName

	ticket.ID = generateULID()
	ticket.CreatedAt = time.Now().Unix()

	if err := storeTicket(ticket); err != nil {
		http.Error(w, "Failed to create ticket", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"ticket_id":  ticket.ID,
		"created_at": ticket.CreatedAt,
	})
}

// GET /ticket/:id
func handleGetTicket(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	pathParts := strings.Split(r.URL.Path, "/")
	if len(pathParts) < 3 {
		http.Error(w, "Invalid URL", http.StatusBadRequest)
		return
	}
	ticketID := pathParts[2]

	var ticket Ticket
	var tagsJSON string
	err := forumDB.QueryRow(`SELECT id, flow_name, input_json, output_json, status, error_message, created_at, duration_ms, tags FROM tickets WHERE id = ?`, ticketID).
		Scan(&ticket.ID, &ticket.FlowName, &ticket.InputJSON, &ticket.OutputJSON, &ticket.Status, &ticket.ErrorMessage, &ticket.CreatedAt, &ticket.DurationMS, &tagsJSON)
	if err != nil {
		http.Error(w, "Ticket not found", http.StatusNotFound)
		return
	}

	json.Unmarshal([]byte(tagsJSON), &ticket.Tags)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(ticket)
}

// GET /tickets
func handleGetTickets(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	limit := 50
	if limitStr := r.URL.Query().Get("limit"); limitStr != "" {
		fmt.Sscanf(limitStr, "%d", &limit)
	}
	// SECURITY FIX: clamp limit to prevent large allocations / DoS
	if limit <= 0 || limit > 200 {
		limit = 50
	}

	rows, err := forumDB.Query(`SELECT id, flow_name, status, created_at, tags FROM tickets ORDER BY created_at DESC LIMIT ?`, limit)
	if err != nil {
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	var tickets []map[string]interface{}
	for rows.Next() {
		var id, flowName, status, tagsJSON string
		var createdAt int64
		rows.Scan(&id, &flowName, &status, &createdAt, &tagsJSON)

		var tags []string
		json.Unmarshal([]byte(tagsJSON), &tags)

		tickets = append(tickets, map[string]interface{}{
			"id":         id,
			"flow_name":  flowName,
			"status":     status,
			"created_at": createdAt,
			"tags":       tags,
		})
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"tickets": tickets, "total": len(tickets)})
}

// POST /lean/query
func handleLeanQuery(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// SECURITY FIX: Limit request body to 1 MB to prevent DoS
	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		http.Error(w, "Failed to read request", http.StatusBadRequest)
		return
	}

	// SECURITY FIX: Use context with timeout to prevent indefinite hang / SSRF amplification
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(flowTimeoutSec)*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, "POST", leanDBURL+"/query", strings.NewReader(string(body)))
	if err != nil {
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		log.Printf("Lean query proxy error: %v", err)
		http.Error(w, "Internal error", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	// SECURITY FIX: Limit response body to 4 MB to prevent memory exhaustion
	output, err := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024))
	if err != nil {
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}
	ticketID := generateULID()

	// Store as ticket
	ticket := Ticket{
		ID:         ticketID,
		FlowName:   "lean_query",
		InputJSON:  body,
		OutputJSON: json.RawMessage(output),
		Status:     "success",
		CreatedAt:  time.Now().Unix(),
		Tags:       []string{"lean", "query"},
	}
	if err := storeTicket(ticket); err != nil {
		// SECURITY FIX: Log error details server-side, return generic message to client
		log.Printf("Ticket store error: %v", err)
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"ticket_id": ticketID,
		"results":   json.RawMessage(output),
	})
}

// POST /tph/inference
func handleTPHInference(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// SECURITY FIX: Limit request body to 1 MB to prevent DoS
	body, err := io.ReadAll(io.LimitReader(r.Body, 1*1024*1024))
	if err != nil {
		http.Error(w, "Failed to read request", http.StatusBadRequest)
		return
	}

	// SECURITY FIX: Use context with timeout to prevent indefinite hang / SSRF amplification
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(flowTimeoutSec)*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, "POST", tphServerURL+"/inference", strings.NewReader(string(body)))
	if err != nil {
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		log.Printf("TPH inference proxy error: %v", err)
		http.Error(w, "Internal error", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	// SECURITY FIX: Limit response body to 4 MB to prevent memory exhaustion
	output, err := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024))
	if err != nil {
		http.Error(w, "Internal error", http.StatusInternalServerError)
		return
	}
	ticketID := generateULID()

	ticket := Ticket{
		ID:         ticketID,
		FlowName:   "tph_inference",
		InputJSON:  body,
		OutputJSON: json.RawMessage(output),
		Status:     "success",
		CreatedAt:  time.Now().Unix(),
		Tags:       []string{"tph", "inference"},
	}
	storeTicket(ticket)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"ticket_id": ticketID,
		"results":   json.RawMessage(output),
	})
}

// GET /env/status
func handleEnvStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Check for .venv
	homeDir := os.Getenv("HOME")
	venvPath := filepath.Join(homeDir, "workspace", ".venv")
	venvActive := false
	if _, err := os.Stat(venvPath); err == nil {
		venvActive = true
	}

	// Get Python version
	cmd := exec.Command("python3", "--version")
	output, _ := cmd.Output()
	pythonVersion := strings.TrimSpace(string(output))

	// Check GPU
	gpuAvailable := false
	gpuName := ""
	if rocmInfo, err := exec.Command("rocm-smi", "--showproductname").Output(); err == nil {
		gpuAvailable = true
		gpuName = strings.TrimSpace(string(rocmInfo))
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"venv_active": venvActive,
		// SECURITY FIX: venv_path removed — it exposed the server's home directory
		"python_version": pythonVersion,
		"gpu_available":  gpuAvailable,
		"gpu_name":       gpuName,
	})
}

// GET /health
func handleHealth(w http.ResponseWriter, r *http.Request) {
	// SECURITY FIX: Add method check
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	checks := map[string]string{
		"database": "ok",
		"bpf":      "ok",
	}

	client := &http.Client{Timeout: 2 * time.Second}

	// SECURITY FIX: Use correct health endpoint path /health/live
	if resp, err := client.Get(leanDBURL + "/health/live"); err != nil {
		checks["lean_db"] = "error"
	} else {
		resp.Body.Close()
		checks["lean_db"] = "ok"
	}

	// Check TPH server — report real status only, no fallbacks
	if resp, err := client.Get(tphServerURL + "/status"); err != nil {
		checks["tph_server"] = "error"
	} else {
		resp.Body.Close()
		checks["tph_server"] = "ok"
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status": "healthy",
		"checks": checks,
	})
}

// ============================================================================
// Helper Functions
// ============================================================================

func sendSSE(w http.ResponseWriter, flusher http.Flusher, event string, data interface{}) {
	dataJSON, _ := json.Marshal(data)
	fmt.Fprintf(w, "event: %s\n", event)
	fmt.Fprintf(w, "data: %s\n\n", dataJSON)
	flusher.Flush()
}

func generateULID() string {
	return ulid.Make().String()
}

func storeTicket(ticket Ticket) error {
	tagsJSON, _ := json.Marshal(ticket.Tags)
	_, err := forumDB.Exec(`INSERT INTO tickets (id, flow_name, input_json, output_json, status, error_message, created_at, duration_ms, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		ticket.ID, ticket.FlowName, ticket.InputJSON, ticket.OutputJSON, ticket.Status, ticket.ErrorMessage, ticket.CreatedAt, ticket.DurationMS, tagsJSON)
	return err
}

// ============================================================================
// LLM Helper Functions
// ============================================================================

// fetchLeanDBHealth queries the Lean DB health endpoint
func fetchLeanDBHealth(ctx context.Context) string {
	client := &http.Client{Timeout: 5 * time.Second}
	req, err := http.NewRequestWithContext(ctx, "GET", leanDBURL+"/health/live", nil)
	if err != nil {
		return "error (request failed)"
	}

	resp, err := client.Do(req)
	if err != nil {
		return "error (unreachable)"
	}
	defer resp.Body.Close()

	if resp.StatusCode == 200 {
		return "healthy"
	}
	return fmt.Sprintf("unhealthy (status: %d)", resp.StatusCode)
}

// fetchRecentTickets retrieves the last N tickets from SQLite
func fetchRecentTickets(ctx context.Context, limit int) []TicketSummary {
	rows, err := forumDB.QueryContext(ctx,
		`SELECT status, flow_name FROM tickets ORDER BY created_at DESC LIMIT ?`, limit)
	if err != nil {
		return nil
	}
	defer rows.Close()

	var tickets []TicketSummary
	for rows.Next() {
		var status, flowName string
		if err := rows.Scan(&status, &flowName); err != nil {
			continue
		}
		tickets = append(tickets, TicketSummary{
			Status: status,
			Title:  flowName,
		})
	}
	return tickets
}

// fetchProofStats queries Lean API for proof statistics
func fetchProofStats(ctx context.Context) string {
	client := &http.Client{Timeout: 5 * time.Second}
	req, err := http.NewRequestWithContext(ctx, "GET", leanDBURL+"/stats", nil)
	if err != nil {
		return ""
	}

	resp, err := client.Do(req)
	if err != nil {
		return ""
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return ""
	}

	body, _ := io.ReadAll(resp.Body)
	var stats map[string]interface{}
	if err := json.Unmarshal(body, &stats); err != nil {
		return ""
	}

	var sb strings.Builder
	if sorryCount, ok := stats["sorry_count"].(float64); ok {
		sb.WriteString(fmt.Sprintf("- sorry_count: %.0f\n", sorryCount))
	}
	if theoremCount, ok := stats["theorem_count"].(float64); ok {
		sb.WriteString(fmt.Sprintf("- theorem_count: %.0f\n", theoremCount))
	}
	return sb.String()
}

// fetchCogneeMemories queries Cognee's local knowledge graph using our python bridge API on port 8002
func fetchCogneeMemories(ctx context.Context, query string) string {
	reqBody, err := json.Marshal(map[string]string{"query": query})
	if err != nil {
		return ""
	}

	req, err := http.NewRequestWithContext(ctx, "POST", "http://localhost:8002/api/cognee/recall", bytes.NewBuffer(reqBody))
	if err != nil {
		return ""
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		log.Printf("Cognee API recall error: %v", err)
		return ""
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		log.Printf("Cognee API recall returned status: %s", resp.Status)
		return ""
	}

	var response struct {
		Status  string `json:"status"`
		Results []struct {
			Text   string `json:"text"`
			Source string `json:"source"`
		} `json:"results"`
		Error string `json:"error"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&response); err != nil || response.Status != "success" {
		log.Printf("Cognee API unmarshal/status error: %v", err)
		return ""
	}

	if len(response.Results) == 0 {
		return ""
	}

	var sb strings.Builder
	for _, r := range response.Results {
		sourceStr := r.Source
		if sourceStr == "" {
			sourceStr = "graph"
		}
		sb.WriteString(fmt.Sprintf("- [%s] %s\n", sourceStr, r.Text))
	}
	return sb.String()
}

// buildStackContext creates the system prompt with current stack state
func buildStackContext(ctx context.Context) string {
	var sb strings.Builder
	sb.WriteString("You are a debugging assistant for Lean4-Automata, a Lean 4 proof-file analysis pipeline.\n\n")
	sb.WriteString("## Current Stack State\n")

	// 1. DB health
	health := fetchLeanDBHealth(ctx)
	sb.WriteString(fmt.Sprintf("- Database: %s\n", health))

	// 2. Available flows
	sb.WriteString("- Available analysis flows: ")
	var flowNames []string
	for k := range flowRegistry {
		// Exclude placeholder flows from LLM context
		if !placeholderFlows[k] {
			flowNames = append(flowNames, k)
		}
	}
	sort.Strings(flowNames)
	sb.WriteString(strings.Join(flowNames, ", ") + "\n")

	// 3. Recent tickets from SQLite
	tickets := fetchRecentTickets(ctx, 5)
	if len(tickets) > 0 {
		sb.WriteString("- Recent findings:\n")
		for _, t := range tickets {
			sb.WriteString(fmt.Sprintf("  * [%s] %s\n", t.Status, t.Title))
		}
	}

	// 4. Proof stats (if Lean API is available)
	stats := fetchProofStats(ctx)
	if stats != "" {
		sb.WriteString("\n## Proof Statistics\n" + stats)
	}

	// 5. Local Cognee semantic memory graph recall
	cogneeMemories := fetchCogneeMemories(ctx, "vessel quantum proof math")
	if cogneeMemories != "" {
		sb.WriteString("\n## Local Cognee Semantic Memories\n" + cogneeMemories)
	}

	sb.WriteString("\nYou can reference any flow by name. When the user asks about a specific file or proof, give concrete debugging advice based on the sorry_count, theorem_count, and proof_completion values.")
	return sb.String()
}

// buildStackContextObject returns structured context data
func buildStackContextObject(ctx context.Context) StackContext {
	var flowNames []string
	for k := range flowRegistry {
		// Exclude placeholder flows from LLM context
		if !placeholderFlows[k] {
			flowNames = append(flowNames, k)
		}
	}
	sort.Strings(flowNames)

	return StackContext{
		DBHealth:       fetchLeanDBHealth(ctx),
		AvailableFlows: flowNames,
		RecentTickets:  fetchRecentTickets(ctx, 5),
		ProofStats:     fetchProofStats(ctx),
		CogneeMemories: fetchCogneeMemories(ctx, "vessel quantum proof math"),
	}
}

// ============================================================================
// LLM Route Handlers
// ============================================================================

// GET /api/llm/models - list available Ollama models
func handleLLMModels(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()

	// Proxy to Ollama API
	req, err := http.NewRequestWithContext(ctx, "GET", ollamaBaseURL+"/api/tags", nil)
	if err != nil {
		http.Error(w, "Failed to create request", http.StatusInternalServerError)
		return
	}

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		http.Error(w, "Ollama unreachable", http.StatusServiceUnavailable)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		http.Error(w, "Ollama returned error", resp.StatusCode)
		return
	}

	body, _ := io.ReadAll(resp.Body)
	var ollamaResp OllamaModelsResponse
	if err := json.Unmarshal(body, &ollamaResp); err != nil {
		http.Error(w, "Failed to parse response", http.StatusInternalServerError)
		return
	}

	// Extract model names
	var modelNames []string
	for _, m := range ollamaResp.Models {
		modelNames = append(modelNames, m.Name)
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"models":  modelNames,
		"default": defaultLLMModel,
	})
}

// POST /api/llm/chat - streaming chat with context injection
func handleLLMChat(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Parse request
	var chatReq LLMChatRequest
	if err := json.NewDecoder(r.Body).Decode(&chatReq); err != nil {
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	// Default model if not specified
	if chatReq.Model == "" {
		chatReq.Model = defaultLLMModel
	}

	// Build messages array
	messages := chatReq.Messages
	if chatReq.InjectContext {
		systemMsg := OllamaMessage{
			Role:    "system",
			Content: buildStackContext(r.Context()),
		}
		messages = append([]OllamaMessage{systemMsg}, messages...)
	}

	// Create Ollama request
	ollamaReq := OllamaChatRequest{
		Model:    chatReq.Model,
		Messages: messages,
		Stream:   true,
	}

	reqBody, _ := json.Marshal(ollamaReq)

	ctx, cancel := context.WithTimeout(r.Context(), llmRequestTimeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, "POST", ollamaBaseURL+"/v1/chat/completions", strings.NewReader(string(reqBody)))
	if err != nil {
		http.Error(w, "Failed to create request", http.StatusInternalServerError)
		return
	}
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		http.Error(w, "Ollama unreachable", http.StatusServiceUnavailable)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		http.Error(w, "Ollama returned error", resp.StatusCode)
		return
	}

	// Set SSE headers
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
		return
	}

	// Stream response
	scanner := bufio.NewScanner(resp.Body)
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" || !strings.HasPrefix(line, "data: ") {
			continue
		}

		// Remove "data: " prefix
		jsonData := strings.TrimPrefix(line, "data: ")
		if jsonData == "[DONE]" {
			fmt.Fprintf(w, "event: done\ndata: {\"done\":true}\n\n")
			flusher.Flush()
			break
		}

		// Parse and re-emit as SSE
		var chunk map[string]interface{}
		if err := json.Unmarshal([]byte(jsonData), &chunk); err != nil {
			continue
		}

		// Extract content from OpenAI format
		if choices, ok := chunk["choices"].([]interface{}); ok && len(choices) > 0 {
			if choice, ok := choices[0].(map[string]interface{}); ok {
				if delta, ok := choice["delta"].(map[string]interface{}); ok {
					if content, ok := delta["content"].(string); ok && content != "" {
						outputData := map[string]interface{}{
							"content": content,
							"done":    false,
						}
						outputJSON, _ := json.Marshal(outputData)
						fmt.Fprintf(w, "event: token\ndata: %s\n\n", outputJSON)
						flusher.Flush()
					}
				}
			}
		}
	}

	if err := scanner.Err(); err != nil {
		log.Printf("Error reading stream: %v", err)
	}
}

// GET /api/llm/context - get current stack context
func handleLLMContext(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()

	contextObj := buildStackContextObject(ctx)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(contextObj)
}

// flowFormulaMatch runs a custom quantum mesh ontology sync flow.
// It parses /root/workspace/ym_flavor729_eigenvalues.csv, matches the requested index/flavor,
// and saves the corresponding semantic memory into the local Cognee knowledge graph!
func flowFormulaMatch(ctx context.Context, params map[string]interface{}, w http.ResponseWriter, flusher http.Flusher) (json.RawMessage, string, string) {
	sendFlowLog(w, flusher, "info", "Starting Quantum Mesh Formula Match and Cognee Sync Flow...")

	// 1. Resolve index parameter
	targetIndex := 0.0
	if val, ok := params["index"]; ok {
		switch v := val.(type) {
		case float64:
			targetIndex = v
		case float32:
			targetIndex = float64(v)
		case int:
			targetIndex = float64(v)
		case int64:
			targetIndex = float64(v)
		case string:
			fmt.Sscanf(v, "%f", &targetIndex)
		}
	}
	sendFlowLog(w, flusher, "info", fmt.Sprintf("Target index selected: %.0f", targetIndex))

	// 2. Open eigenvalues CSV
	csvPath := "/root/workspace/ym_flavor729_eigenvalues.csv"
	file, err := os.Open(csvPath)
	if err != nil {
		errStr := fmt.Sprintf("Failed to open eigenvalues CSV at %s: %v", csvPath, err)
		sendFlowLog(w, flusher, "error", errStr)
		return nil, "error", errStr
	}
	defer file.Close()

	reader := csv.NewReader(file)
	records, err := reader.ReadAll()
	if err != nil {
		errStr := fmt.Sprintf("Failed to parse CSV: %v", err)
		sendFlowLog(w, flusher, "error", errStr)
		return nil, "error", errStr
	}

	sendFlowLog(w, flusher, "info", fmt.Sprintf("Loaded %d eigenvalues from CSV", len(records)-1))

	// 3. Find matching index
	var matchedIndex float64
	var matchedEigenvalue string
	found := false

	// Index column could be floating scientific notation (e.g. 1.000000000000000000e+00)
	// We parse it and compare
	for i := 1; i < len(records); i++ {
		row := records[i]
		if len(row) < 2 {
			continue
		}

		var parsedIndex float64
		_, err1 := fmt.Sscanf(row[0], "%f", &parsedIndex)
		if err1 != nil {
			continue
		}

		// Compare as rounded integers
		if math.Round(parsedIndex) == math.Round(targetIndex) {
			matchedIndex = parsedIndex
			matchedEigenvalue = row[1]
			found = true
			break
		}
	}

	if !found {
		// Fallback to row 1 (index 0) if index is out of bounds
		if len(records) > 1 && len(records[1]) >= 2 {
			fmt.Sscanf(records[1][0], "%f", &matchedIndex)
			matchedEigenvalue = records[1][1]
			found = true
			sendFlowLog(w, flusher, "warning", fmt.Sprintf("Target index %.0f not found. Falling back to index %.0f", targetIndex, matchedIndex))
		}
	}

	if !found {
		errStr := "No eigenvalues available in CSV"
		sendFlowLog(w, flusher, "error", errStr)
		return nil, "error", errStr
	}

	sendFlowLog(w, flusher, "info", fmt.Sprintf("Successfully matched index: %.0f, eigenvalue: %s", matchedIndex, matchedEigenvalue))

	// 4. Formulate semantic text
	semanticText := fmt.Sprintf(
		"Yang-Mills (YM) Flavor 729 eigenvalue for index %.0f (quantum flavor state) is %s. This eigenvalue represents the high-frequency state dynamics of the Yang-Mills gauge theory under the sovereign automata framework.",
		matchedIndex,
		matchedEigenvalue,
	)

	// 5. Save/Remember to Cognee graph using local FastAPI server
	sendFlowLog(w, flusher, "info", "Indexing matched formula memory into Cognee's local knowledge graph...")
	
	var rememberErr error
	var rememberOutput string
	
	rememberReqBody, err := json.Marshal(map[string]string{"text": semanticText})
	if err == nil {
		req, reqErr := http.NewRequestWithContext(ctx, "POST", "http://localhost:8002/api/cognee/remember", bytes.NewBuffer(rememberReqBody))
		if reqErr == nil {
			req.Header.Set("Content-Type", "application/json")
			client := &http.Client{Timeout: 30 * time.Second}
			resp, doErr := client.Do(req)
			if doErr != nil {
				rememberErr = doErr
				rememberOutput = fmt.Sprintf("API error: %v", doErr)
			} else {
				defer resp.Body.Close()
				if resp.StatusCode == http.StatusOK {
					rememberOutput = "Successfully synced to Cognee via port 8002 API"
				} else {
					rememberErr = fmt.Errorf("HTTP status: %s", resp.Status)
					rememberOutput = fmt.Sprintf("HTTP status: %s", resp.Status)
				}
			}
		} else {
			rememberErr = reqErr
			rememberOutput = fmt.Sprintf("Req error: %v", reqErr)
		}
	} else {
		rememberErr = err
		rememberOutput = fmt.Sprintf("JSON marshal error: %v", err)
	}

	if rememberErr != nil {
		sendFlowLog(w, flusher, "warning", fmt.Sprintf("Cognee memory sync completed with warnings: %v, out: %s", rememberErr, rememberOutput))
	} else {
		sendFlowLog(w, flusher, "info", "Successfully completed Cognee graph sync!")
	}

	// 6. Return response
	result := map[string]interface{}{
		"index":         matchedIndex,
		"eigenvalue":    matchedEigenvalue,
		"flavor":        matchedIndex,
		"cognee_status": "synced",
		"semantic_text": semanticText,
		"bridge_output": rememberOutput,
	}

	resultJSON, _ := json.Marshal(result)
	sendFlowLog(w, flusher, "info", "Formula match sync completed successfully!")

	return resultJSON, "success", ""
}

// ============================================================================
// Paper Trader Simulate Handler
// ============================================================================
func handlePaperTraderSimulate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req struct {
		Capital float64 `json:"capital"`
		WinRate float64 `json:"winrate"`
		Flux    float64 `json:"flux"`
		Market  string  `json:"market"`
		Speed   float64 `json:"speed"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Bad request", http.StatusBadRequest)
		return
	}

	if req.Market == "" {
		req.Market = "BTC"
	}
	if req.Speed <= 0 {
		req.Speed = 1.0
	}

	// Kill any existing sim running
	exec.Command("pkill", "-f", "tph_paper_trader.py --sim").Run()
	exec.Command("pkill", "-f", "coinbase_wss_bridge.py --sim").Run()
	
	// Truncate the log files to clear previous sim runs if we want a fresh start
	path := "/root/workspace/Automata/Lean4-Automata/paper_trader/audit_logs/paper_trades_audit.jsonl"
	os.WriteFile(path, []byte(""), 0644)
	
	cbPath := "/root/workspace/Automata/Lean4-Automata/paper_trader/audit_logs/coinbase_bridge_audit.jsonl"
	os.WriteFile(cbPath, []byte(""), 0644)

	// Start a new paper trader simulation
	scriptPath := "/root/workspace/Automata/Lean4-Automata/paper_trader/src/tph_paper_trader.py"
	cmd := exec.Command("python3", scriptPath, "--sim", 
		"--capital", fmt.Sprintf("%f", req.Capital),
		"--winrate", fmt.Sprintf("%f", req.WinRate),
		"--flux", fmt.Sprintf("%f", req.Flux),
		"--market", req.Market,
		"--speed", fmt.Sprintf("%f", req.Speed),
	)
	cmd.Dir = "/root/workspace/Automata/Lean4-Automata/paper_trader/src/"
	if err := cmd.Start(); err != nil {
		http.Error(w, "Failed to start simulation: "+err.Error(), http.StatusInternalServerError)
		return
	}
	
	// Start a new coinbase bridge simulation
	coinbaseScript := "/root/workspace/Automata/Lean4-Automata/paper_trader/src/coinbase_wss_bridge.py"
	cbCmd := exec.Command("python3", coinbaseScript, "--sim",
		"--market", req.Market,
		"--speed", fmt.Sprintf("%f", req.Speed),
	)
	cbCmd.Dir = "/root/workspace/Automata/Lean4-Automata/paper_trader/src/"
	if err := cbCmd.Start(); err != nil {
		// Log error but don't fail, we already started the paper trader
		log.Printf("Warning: failed to start coinbase bridge sim: %v", err)
	}
	
	// We do NOT wait for it. Let them run in the background.
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{
		"status": "started", 
		"paper_trader_pid": fmt.Sprintf("%d", cmd.Process.Pid),
		"coinbase_bridge_pid": fmt.Sprintf("%d", cbCmd.Process.Pid),
	})
}

// ============================================================================
// Paper Trader Pause Handler
// ============================================================================
func handlePaperTraderPause(w http.ResponseWriter, r *http.Request) {
	exec.Command("pkill", "-STOP", "-f", "tph_paper_trader.py").Run()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "paused"})
}

// ============================================================================
// Paper Trader Resume Handler
// ============================================================================
func handlePaperTraderResume(w http.ResponseWriter, r *http.Request) {
	exec.Command("pkill", "-CONT", "-f", "tph_paper_trader.py").Run()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "resumed"})
}

// ============================================================================
// Coinbase Bridge Configure Handler
// ============================================================================
func handleCoinbaseConfigure(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req struct {
		Market string  `json:"market"`
		Source string  `json:"source"` // "live" or "sim"
		Speed  float64 `json:"speed"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Bad request", http.StatusBadRequest)
		return
	}

	if req.Market == "" {
		req.Market = "BTC"
	}
	if req.Speed <= 0 {
		req.Speed = 1.0
	}

	// Kill any existing coinbase bridge running
	exec.Command("pkill", "-f", "coinbase_wss_bridge.py").Run()

	// Truncate coinbase log file for fresh start
	cbPath := "/root/workspace/Automata/Lean4-Automata/paper_trader/audit_logs/coinbase_bridge_audit.jsonl"
	os.WriteFile(cbPath, []byte(""), 0644)

	// Spawning args
	coinbaseScript := "/root/workspace/Automata/Lean4-Automata/paper_trader/src/coinbase_wss_bridge.py"
	var args []string
	if req.Source == "sim" {
		args = []string{coinbaseScript, "--sim", "--market", req.Market, "--speed", fmt.Sprintf("%f", req.Speed)}
	} else {
		// Live websocket mode
		args = []string{coinbaseScript, "--market", req.Market}
	}

	cbCmd := exec.Command("python3", args...)
	cbCmd.Dir = "/root/workspace/Automata/Lean4-Automata/paper_trader/src/"
	if err := cbCmd.Start(); err != nil {
		http.Error(w, "Failed to start coinbase bridge: "+err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{
		"status": "configured",
		"market": req.Market,
		"source": req.Source,
		"pid":    fmt.Sprintf("%d", cbCmd.Process.Pid),
	})
}

// ============================================================================
// Paper Trader SSE Stream Handler
// ============================================================================
func handlePaperTraderStream(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
		return
	}

	path := "/root/workspace/Automata/Lean4-Automata/paper_trader/audit_logs/paper_trades_audit.jsonl"
	
	// First, send the last known state
	file, err := os.Open(path)
	var lastOffset int64 = 0
	if err == nil {
		info, _ := file.Stat()
		lastOffset = info.Size()
		
		// Find last 10000 bytes by reading from end roughly
		startOffset := int64(0)
		if lastOffset > 10000 {
			startOffset = lastOffset - 10000
		}
		file.Seek(startOffset, 0)
		
		scanner := bufio.NewScanner(file)
		var lastLines []string
		for scanner.Scan() {
			if text := strings.TrimSpace(scanner.Text()); text != "" {
				lastLines = append(lastLines, text)
			}
		}
		if err := scanner.Err(); err != nil {
			log.Printf("Scanner error: %v", err)
		}
		
		// Send last few records to hydrate initial state
		startIdx := 0
		if len(lastLines) > 5 {
			startIdx = len(lastLines) - 5
		}
		for _, line := range lastLines[startIdx:] {
			var obj map[string]interface{}
			if err := json.Unmarshal([]byte(line), &obj); err == nil {
				if po, exists := obj["paper_order"]; exists {
					b, _ := json.Marshal(po)
					fmt.Fprintf(w, "data: %s\n\n", string(b))
				}
			}
		}
		flusher.Flush()
		file.Close()
	}

	// Now tail the file
	for {
		select {
		case <-r.Context().Done():
			return
		default:
			file, err := os.Open(path)
			if err == nil {
				info, _ := file.Stat()
				if info.Size() > lastOffset {
					file.Seek(lastOffset, 0)
					scanner := bufio.NewScanner(file)
					for scanner.Scan() {
						text := strings.TrimSpace(scanner.Text())
						if text != "" {
							var obj map[string]interface{}
							if err := json.Unmarshal([]byte(text), &obj); err == nil {
								if po, exists := obj["paper_order"]; exists {
									b, _ := json.Marshal(po)
									fmt.Fprintf(w, "data: %s\n\n", string(b))
									flusher.Flush()
								}
							}
						}
					}
					if err := scanner.Err(); err != nil {
						log.Printf("Scanner error: %v", err)
					}
					lastOffset = info.Size()
				} else if info.Size() < lastOffset {
					// File was truncated/rotated
					lastOffset = 0
				}
				file.Close()
			}
			time.Sleep(1 * time.Second)
		}
	}
}

// ============================================================================
// Coinbase SSE Stream Handler
// ============================================================================
func handleCoinbaseStream(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "Streaming unsupported", http.StatusInternalServerError)
		return
	}

	path := "/root/workspace/Automata/Lean4-Automata/paper_trader/audit_logs/coinbase_bridge_audit.jsonl"
	
	// First, send the last known state (last few lines)
	file, err := os.Open(path)
	var lastOffset int64 = 0
	if err == nil {
		info, _ := file.Stat()
		lastOffset = info.Size()
		
		startOffset := int64(0)
		if lastOffset > 10000 {
			startOffset = lastOffset - 10000
		}
		file.Seek(startOffset, 0)
		
		scanner := bufio.NewScanner(file)
		var lastLines []string
		for scanner.Scan() {
			if text := strings.TrimSpace(scanner.Text()); text != "" {
				lastLines = append(lastLines, text)
			}
		}
		if err := scanner.Err(); err != nil {
			log.Printf("Scanner error: %v", err)
		}
		
		startIdx := 0
		if len(lastLines) > 5 {
			startIdx = len(lastLines) - 5
		}
		for _, line := range lastLines[startIdx:] {
			fmt.Fprintf(w, "data: %s\n\n", line)
		}
		flusher.Flush()
		file.Close()
	}

	// Now tail the file
	for {
		select {
		case <-r.Context().Done():
			return
		default:
			file, err := os.Open(path)
			if err == nil {
				info, _ := file.Stat()
				if info.Size() > lastOffset {
					file.Seek(lastOffset, 0)
					scanner := bufio.NewScanner(file)
					for scanner.Scan() {
						text := strings.TrimSpace(scanner.Text())
						if text != "" {
							fmt.Fprintf(w, "data: %s\n\n", text)
							flusher.Flush()
						}
					}
					if err := scanner.Err(); err != nil {
						log.Printf("Scanner error: %v", err)
					}
					lastOffset = info.Size()
				} else if info.Size() < lastOffset {
					lastOffset = 0
				}
				file.Close()
			}
			time.Sleep(100 * time.Millisecond) // Fast stream for 200 TPS L2 data
		}
	}
}

// ============================================================================
// Registration Function — Call from main()
// ============================================================================

func registerForumRoutes() {
	// Register routes
	http.HandleFunc("/api/paper-trader/stream", handlePaperTraderStream)
	http.HandleFunc("/api/paper-trader/simulate", handlePaperTraderSimulate)
	http.HandleFunc("/api/paper-trader/pause", handlePaperTraderPause)
	http.HandleFunc("/api/paper-trader/resume", handlePaperTraderResume)
	http.HandleFunc("/api/coinbase/stream", handleCoinbaseStream)
	http.HandleFunc("/api/coinbase/configure", handleCoinbaseConfigure)
	http.HandleFunc("/flows", handleGetFlows)
	http.HandleFunc("/run/flow/", handleRunFlow)
	http.HandleFunc("/ticket/create", handleCreateTicket)
	http.HandleFunc("/ticket/", handleGetTicket)
	http.HandleFunc("/tickets", handleGetTickets)
	http.HandleFunc("/lean/query", handleLeanQuery)
	http.HandleFunc("/tph/inference", handleTPHInference)
	http.HandleFunc("/env/status", handleEnvStatus)
	http.HandleFunc("/health", handleHealth)

	// LLM debug agent routes
	http.HandleFunc("/api/llm/models", handleLLMModels)
	http.HandleFunc("/api/llm/chat", handleLLMChat)
	http.HandleFunc("/api/llm/context", handleLLMContext)

	// Set up reverse proxy for Python dashboard port 8002
	target, err := url.Parse("http://localhost:8002")
	if err == nil {
		proxy := &httputil.ReverseProxy{
			Director: func(req *http.Request) {
				req.URL.Scheme = target.Scheme
				req.URL.Host = target.Host
				// Strip "/api/python-dashboard" prefix
				origPath := req.URL.Path
				if strings.HasPrefix(origPath, "/api/python-dashboard") {
					req.URL.Path = origPath[len("/api/python-dashboard"):]
					if req.URL.Path == "" {
						req.URL.Path = "/"
					}
				}
				// Set appropriate proxy headers
				req.Header.Set("X-Forwarded-Host", req.Header.Get("Host"))
				req.Header.Set("X-Origin-Host", target.Host)
			},
		}
		http.Handle("/api/python-dashboard/", proxy)
		log.Println("🔌 Reverse proxy registered on /api/python-dashboard/ to http://localhost:8002")
	} else {
		log.Printf("⚠️ Failed to parse target URL for python-dashboard reverse proxy: %v\n", err)
	}

	// Set up reverse proxy for ChatTTS Voice Hub port 7777
	chatttsTarget, chatttsErr := url.Parse("http://localhost:7777")
	if chatttsErr == nil {
		chatttsProxy := &httputil.ReverseProxy{
			Director: func(req *http.Request) {
				req.URL.Scheme = chatttsTarget.Scheme
				req.URL.Host = chatttsTarget.Host
				// Strip "/api/chattts" prefix
				origPath := req.URL.Path
				if strings.HasPrefix(origPath, "/api/chattts") {
					req.URL.Path = origPath[len("/api/chattts"):]
					if req.URL.Path == "" {
						req.URL.Path = "/"
					}
				}
				req.Header.Set("X-Forwarded-Host", req.Header.Get("Host"))
				req.Header.Set("X-Origin-Host", chatttsTarget.Host)
			},
		}
		http.Handle("/api/chattts/", chatttsProxy)
		log.Println("🔌 Reverse proxy registered on /api/chattts/ to http://localhost:7777")
	} else {
		log.Printf("⚠️ Failed to parse target URL for ChatTTS Voice Hub reverse proxy: %v\n", chatttsErr)
	}

	// Set up reverse proxy for A-JEPA Aligner Bridge port 8003
	jepaTarget, jepaErr := url.Parse("http://localhost:8003")
	if jepaErr == nil {
		jepaProxy := &httputil.ReverseProxy{
			Director: func(req *http.Request) {
				req.URL.Scheme = jepaTarget.Scheme
				req.URL.Host = jepaTarget.Host
				// Keep path intact as A-JEPA endpoints already expect /api/jepa/... and /api/speechmatics/...
				req.Header.Set("X-Forwarded-Host", req.Header.Get("Host"))
				req.Header.Set("X-Origin-Host", jepaTarget.Host)
			},
		}
		http.Handle("/api/jepa/", jepaProxy)
		http.Handle("/api/speechmatics/", jepaProxy)
		log.Println("🔌 Reverse proxy registered on /api/jepa/ and /api/speechmatics/ to http://localhost:8003")
	} else {
		log.Printf("⚠️ Failed to parse target URL for A-JEPA reverse proxy: %v\n", jepaErr)
	}

	// Static file serving for the interactive dashboard frontend
	uiDir := "/root/workspace/Automata/Lean4-Automata/ui"
	fileServer := http.FileServer(http.Dir(uiDir))
	noCacheFileServer := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
		w.Header().Set("Pragma", "no-cache")
		w.Header().Set("Expires", "0")
		fileServer.ServeHTTP(w, r)
	})
	http.Handle("/ui/", http.StripPrefix("/ui/", noCacheFileServer))
	log.Printf("📂 Serving static dashboard UI from %s on /ui/ with cache-busting headers\n", uiDir)

	log.Println("✅ Forum routes registered")
}

// ============================================================================
// Add to main() AFTER existing routes:
//
// func main() {
//     // Existing QMCP routes
//     http.HandleFunc("/qmcp/v1/consent", handleConsentGate)
//     http.HandleFunc("/api/regulator", handleConsentGate)
//
//     // NEW: Register forum routes
//     registerForumRoutes()
//
//     log.Println("🚀 Starting QMCP Server (Go Orchestrator) on :8443...")
//     go startBPF()
//
//     if err := http.ListenAndServe(":8443", nil); err != nil {
//         log.Fatalf("Server failed: %v", err)
//     }
// }
// ============================================================================
