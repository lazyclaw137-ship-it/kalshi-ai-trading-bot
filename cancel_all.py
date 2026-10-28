"""One-shot cancel-all. Also sets the 60 s wait in state.json."""
import os, time, json
import state, bot

if __name__ == "__main__":
    cfg = bot.load_config()
    broker = bot.make_broker(cfg, bot.make_logger("logs/decisions.jsonl"))
    broker.cancel_all()
    st = state.load()
    st["resume_after"] = time.time() + 60
    state.save(st)
    print("cancel-all sent; the bot waits 60 s before ordering again")
