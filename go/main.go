package main

import (
	"log"
	"net/http"
)

// corsMiddleware wraps DefaultServeMux to allow file:// and localhost origins.
func corsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, X-Request-ID")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func main() {
	if err := initForumDB(); err != nil {
		log.Fatalf("failed to initialize forum database: %v", err)
	}

	registerForumRoutes()

	addr := getEnv("LEAN4A_FORUM_ADDR", ":8443")
	log.Printf("Lean4-Automata forum harness listening on %s", addr)
	if err := http.ListenAndServe(addr, corsMiddleware(http.DefaultServeMux)); err != nil {
		log.Fatalf("forum harness stopped: %v", err)
	}
}
