import asyncio
import time
import random
import httpx
import uuid

AGENT_ID = "00000000-0000-0000-0000-000000000001"
MANDATE_ID = "00000000-0000-0000-0000-000000000002"

async def simulate_request(client, i):
    tx_id = str(uuid.uuid4())
    payload = {
        "mandate_id": MANDATE_ID,
        "bypass_agentguard": False,
        "transaction": {
            "tx_id": tx_id,
            "agent_id": AGENT_ID,
            "mandate_id": MANDATE_ID,
            "amount": random.uniform(10.0, 500.0),
            "merchant_id": "merchant_electronics_01",
            "category": "electronics",
            "purpose_code": "GDDS",
            "remittance_text": f"Test purchase {i}"
        },
        "content_to_check": "Standard item purchase"
    }
    
    t0 = time.perf_counter()
    try:
        response = await client.post("http://127.0.0.1:8000/transactions/authorize", json=payload, timeout=30.0)
        latency_ms = (time.perf_counter() - t0) * 1000
        if response.status_code == 200:
            return {"status": "success", "latency": latency_ms, "decision": response.json().get("decision")}
        else:
            return {"status": "error", "error": f"HTTP {response.status_code}: {response.text}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

async def main():
    concurrency = 100
    print(f"Firing {concurrency} concurrent requests at POST /transactions/authorize...")
    
    # Pre-seed the mandate in the DB using a separate request or just assume it's seeded
    # Wait, the API requires the mandate to exist! We might get 404 Mandate not found.
    # Let's seed the mandate first
    async with httpx.AsyncClient() as c:
        mandate_payload = {
            "mandate_id": MANDATE_ID,
            "agent_id": AGENT_ID,
            "max_amount_per_tx": 1000.0,
            "approved_categories": ["electronics"],
            "approved_merchants": ["merchant_electronics_01"],
            "requires_2fa_above": 500.0
        }
        # Wait, there's no endpoint to seed a mandate in AgentGuard usually, it's injected in DB directly.
        pass
    
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    async with httpx.AsyncClient(limits=limits) as client:
        t0 = time.perf_counter()
        tasks = [simulate_request(client, i) for i in range(concurrency)]
        results = await asyncio.gather(*tasks)
        total_time = time.perf_counter() - t0
        
    success_count = sum(1 for r in results if r["status"] == "success")
    error_count = sum(1 for r in results if r["status"] == "error")
    
    print("\n=== Load Test Results ===")
    print(f"Total Requests: {concurrency}")
    print(f"Successful:     {success_count}")
    print(f"Errors:         {error_count}")
    print(f"Total Time:     {total_time:.2f} seconds")
    print(f"Throughput:     {concurrency / total_time:.2f} req/s")
    
    if success_count > 0:
        latencies = [r["latency"] for r in results if r["status"] == "success"]
        avg_lat = sum(latencies) / len(latencies)
        p99_lat = sorted(latencies)[int(len(latencies) * 0.99)] if len(latencies) >= 100 else max(latencies)
        print(f"Avg Latency:    {avg_lat:.2f} ms")
        print(f"P99 Latency:    {p99_lat:.2f} ms")
        
    if error_count > 0:
        print("\nSample Errors:")
        errors = [r["error"] for r in results if r["status"] == "error"]
        for e in set(errors[:5]):
            print(f"  - {e}")

if __name__ == "__main__":
    asyncio.run(main())
