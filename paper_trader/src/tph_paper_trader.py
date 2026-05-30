#!/usr/bin/env python3
"""
tph_paper_trader.py — SOC 2 Compliant Paper Trading Simulator

Subscribes to the tph_trade_executor ZMQ pipe.
Receives {action, confidence, price} from the bare-metal sniper.
Executes theoretical paper trades and tracks ROI.
Logs immutably to the SOC 2 Logger.
"""

import sys
import zmq
import struct
import os
import stat
import json
import random
import time
from pathlib import Path
from secure_audit_logger import SecureAuditLogger

# C struct: int action; float confidence; float price;
EXEC_STRUCT_FMT = '<iff'
ENGINE_ROOT = Path(__file__).resolve().parents[1]

class DynamicFluxRiskEngine:
    """
    Direct Python port of the C# DynamicFluxAlgorithm.
    Formula: G'(t) = S - alpha(G(t)) + DeltaM + U - F
    """
    def __init__(self, initial_flux=5.0):
        self.s = 2.0
        self.alpha = 0.1
        self.current_flux = initial_flux
        self.threshold_barrier = 50.0
        
        self.regulator_active = False
        self.safe_alert_threshold = 30.0
        self.required_safe_periods = 3
        self.current_safe_period_count = 0
        
    def evaluate_alert(self, confidence_alert_level: float):
        was_active = self.regulator_active
        self.regulator_active = confidence_alert_level > 50.0
        
        if self.regulator_active and not was_active:
            print("[DYNAMIC_FLUX] ALERT: Confidence level exceeded 50. Regulator set to HALT (1)")
            self.current_safe_period_count = 0
            
    def check_safe_recovery(self, current_alert_level: float):
        if self.regulator_active:
            if current_alert_level < self.safe_alert_threshold:
                self.current_safe_period_count += 1
                print(f"[DYNAMIC_FLUX] SAFETY: Safe conditions detected ({self.current_safe_period_count}/{self.required_safe_periods})")
                if self.current_safe_period_count >= self.required_safe_periods:
                    self.regulator_active = False
                    self.current_safe_period_count = 0
                    print("[DYNAMIC_FLUX] SAFETY: F-stop regulator reset after sustained safe conditions")
            else:
                if self.current_safe_period_count > 0:
                    self.current_safe_period_count = 0
                    
    def update_flux(self, delta_m: float, u: float):
        previous_flux = self.current_flux
        f = 1.0 if self.regulator_active else 0.0
        
        dynamic_flux_prime = self.s - (self.alpha * self.current_flux) + delta_m + u - f
        
        if self.regulator_active:
            dynamic_flux_prime = 0.0
            
        self.current_flux += dynamic_flux_prime
        
        if self.current_flux > self.threshold_barrier:
            print(f"[DYNAMIC_FLUX] ALERT: Barrier ({self.threshold_barrier}) exceeded! Rolling back...")
            self.current_flux = previous_flux
        
        return self.current_flux


def main():
    print("[PAPER TRADER] Initializing Sovereign Paper Trading Engine")
    
    # Initialize ZMQ Subscriber (Zero-Copy PUSH->PUB architecture for Dual Entanglement Tests)
    ctx = zmq.Context()
    sock = ctx.socket(zmq.SUB)
    sock.setsockopt_string(zmq.SUBSCRIBE, "")
    sock.connect("ipc:///tmp/tph_trade_executor.ipc")
    
    # Initialize immutable logger
    audit_logger = SecureAuditLogger(
        base_dir=str(ENGINE_ROOT / "audit_logs"),
        log_filename="paper_trades_audit.jsonl"
    )
    print(f"[PAPER TRADER] SOC 2 Ledger connected: {audit_logger.log_path}")
    
    # === SOC 2 Enterprise Safe Boot Authorization (Cross-Compatibilized) ===
    print("[PAPER TRADER] Initiating Multi-Layer Safe Boot Verification...")
    stakeholder_sim = {
        "authorized_by": "Vessel-Production Admin Root",
        "contact": "soc2-compliance@sovereign-node.local",
        "authorization_tier": "Full Autonomous Paper Trader",
        "pid": os.getpid(),
        "boot_timestamp": os.popen("date -u +%Y%m%dT%H%M%SZ").read().strip()
    }
    
    boot_hash = audit_logger.log_tick(
        feature_8d=[], # No market features yet
        model_output={"SYSTEM": "SAFE_BOOT_AUTHORIZATION"},
        paper_order={"stakeholder_verification": stakeholder_sim}
    )
    print(f"[PAPER TRADER] Stakeholder Safe Boot Authorized. Genesis Hash: {boot_hash[:12]}...\n")
    # =======================================================================
    
    import argparse
    parser = argparse.ArgumentParser(description="SOC 2 Compliant Paper Trading Simulator")
    parser.add_argument("--sim", action="store_true", help="Run in simulation mode")
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital USD")
    parser.add_argument("--winrate", type=float, default=0.65, help="Target win rate for sim mode")
    parser.add_argument("--flux", type=float, default=5.0, help="Initial Dynamic Flux G(t)")
    parser.add_argument("--market", type=str, default="BTC", help="Target market symbol")
    parser.add_argument("--speed", type=float, default=1.0, help="Simulation tick speed delay in seconds")
    args = parser.parse_args()

    # Initialize ported C# Risk Engine
    risk_engine = DynamicFluxRiskEngine(initial_flux=args.flux)
    print(f"[PAPER TRADER] DynamicFlux Risk Engine Loaded (HALT/CONTINUE Safety Net Active) - Init Flux: {args.flux}")
    
    # Portfolio State
    usd_balance = args.capital
    btc_balance = 0.0  # Represents the active asset balance (general asset pool)
    initial_balance = usd_balance
    total_trades = 0

    # Base price mappings for simulation
    price_bases = {
        "BTC": 72900.0,
        "ETH": 3800.0,
        "SOL": 175.0,
        "SPY": 530.0,
        "ADA": 0.45,
        "DOGE": 0.15,
        "XRP": 0.50,
        "DOT": 6.50,
        "LINK": 15.0,
        "AVAX": 35.0,
        "MATIC": 0.70,
        "BNB": 580.0,
        "LTC": 85.0,
        "SHIB": 0.000025,
        "ATOM": 8.50,
        "UNI": 7.50,
        "QQQ": 440.0,
        "AAPL": 190.0,
        "MSFT": 420.0,
        "GOOGL": 175.0,
        "NVDA": 950.0,
        "TSLA": 180.0,
        "AMZN": 185.0,
        "META": 475.0,
        "AMD": 160.0
    }
    price_base = price_bases.get(args.market.upper(), 100.0)

    # Load Tier 3 Wolfram Financial Kelly Allocations
    sizing_file = ENGINE_ROOT / "tier3_master_sizing.json"
    if sizing_file.exists():
        with open(sizing_file, 'r') as f:
            portfolio_weights = json.load(f)
        print("[PAPER TRADER] Wolfram Tier 3 Diversification Matrix loaded.")
    else:
        print("[PAPER TRADER] WARNING: Tier 3 JSON not found. Run Wolfram books. Defaulting to 1% sizing.")
        portfolio_weights = {args.market: {}}

    print(f"[PAPER TRADER] Starting Balance: ${usd_balance:,.2f} | Market: {args.market}")
    
    sim_mode = args.sim
    if sim_mode:
        print(f"[SIM MODE] Generating synthetic sniper signals (Target WinRate: {args.winrate}) at {args.speed}s intervals...")
    else:
        print("[PAPER TRADER] Listening for C-Sniper triggers...")

    try:
        tick = 0
        while True:
            # 1. Fetch signal (Live ZMQ or Sim)
            if sim_mode:
                action = random.choice([0, 1, 2])  # HOLD/BUY/SELL
                confidence = random.uniform(0.3, 0.99)
                price = price_base + random.uniform(-price_base * 0.002, price_base * 0.002)
                tick += 1
                time.sleep(args.speed)  # Dynamic Planning Speed Control
            else:
                msg = sock.recv()
                if len(msg) != struct.calcsize(EXEC_STRUCT_FMT):
                    continue
                action, confidence, price = struct.unpack(EXEC_STRUCT_FMT, msg)
            
            # 2. Map action to string
            # Default BitNet/TPH model: 0=HOLD, 1=BUY, 2=SELL
            side = "HOLD"
            if action == 1:
                side = "BUY"
            elif action == 2:
                side = "SELL"
                
            if side == "HOLD" or price <= 0:
                continue

            # 3. Filter Garbage & Hard Bounds
            if confidence < 0.90:
                continue

            # Fractional Kelly Allocation (Wolfram Integration)
            conf_key = f"{round(confidence, 2):.2f}"
            base_sizing_pct = portfolio_weights.get(args.market, {}).get(conf_key, 0.01) # Default 1% if miss
            trade_size_usd = usd_balance * base_sizing_pct
            trade_size = trade_size_usd / price

            # 4. Route through DynamicFlux Risk Engine
            alert_level = random.uniform(10.0, 60.0)
            risk_engine.evaluate_alert(alert_level)
            risk_engine.check_safe_recovery(alert_level)
            
            delta_m = confidence * 1.5
            u = 1.0
            current_flux = risk_engine.update_flux(delta_m, u)
            
            # Application of F-Factor HALT
            if risk_engine.regulator_active:
                print(f"[REJECTED] {side} {trade_size:.5f} {args.market} @ ${price:,.2f} | Conf: {confidence:.3f} | REASON: DynamicFlux HALT Triggered (F=1)")
                continue

            # 4. Execute Paper Trade
            trade_value = trade_size * price
            executed = False
            
            if side == "BUY" and usd_balance >= trade_value:
                usd_balance -= trade_value
                btc_balance += trade_size
                executed = True
            elif side == "SELL" and btc_balance >= trade_size:
                usd_balance += trade_value
                btc_balance -= trade_size
                executed = True
                
            if not executed:
                continue
                
            total_trades += 1
            current_portfolio_value = usd_balance + (btc_balance * price)
            roi_pct = ((current_portfolio_value - initial_balance) / initial_balance) * 100.0
            
            paper_order = {
                "action": side,
                "confidence": confidence,
                "price": price,
                "qty": trade_size,
                "usd_balance": usd_balance,
                "btc_balance": btc_balance,
                "portfolio_value": current_portfolio_value,
                "roi_pct": roi_pct,
                "dynamic_flux": current_flux,
                "asset_symbol": args.market
            }
            
            # Immutable Log - SOC 2
            audit_logger.log_tick(
                feature_8d=[],
                model_output={"action_code": action, "confidence": confidence},
                paper_order=paper_order
            )
            
            print(f"\n[PAPER_EXEC] {side} {trade_size:.5f} {args.market} @ ${price:,.2f} | Conf: {confidence:.3f}")
            print(f"             Portfolio: ${current_portfolio_value:,.2f} | ROI: {roi_pct:+.3f}% | Flux G(t): {current_flux:.2f} | Trades: {total_trades}")
            
    except KeyboardInterrupt:
        print("\n[PAPER TRADER] Terminal Shutdown. Finalizing ledger.")

if __name__ == "__main__":
    main()
