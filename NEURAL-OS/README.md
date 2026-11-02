# NEURAL OS

Local dashboard for ChatGPT and Claude. API keys stay on the local server and
are never sent to the browser.

## Run

1. Copy `.env.example` to `.env`.
2. Add your OpenAI API key as `OPENAI_API_KEY` and/or Anthropic API key as
   `ANTHROPIC_API_KEY`. You can configure either provider independently.
3. Start the server from the project directory:

   ```sh
   python3 scripts/server.py
   ```

4. Open <http://127.0.0.1:8000>.

The server binds to loopback only. Optional `OPENAI_MODEL`, `ANTHROPIC_MODEL`,
and `PORT` environment variables can override the defaults. API usage is billed
by the respective provider.
