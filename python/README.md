# TeenyFactories

**Multi-provider LLM and message queue abstraction for distributed agent systems**

TeenyFactories is a Python package that provides a unified interface for building distributed agent systems with support for multiple LLM providers and message queue backends.

## Features

- 🤖 **Multi-Provider LLM Support**: OpenAI, Anthropic, Google Gemini, Ollama, Azure Bedrock (including O3 models)
- 📬 **Pluggable Message Queues**: Redis pub/sub, PostgreSQL LISTEN/NOTIFY
- 📝 **Standardized Logging**: Unified logging interface with configurable levels
- ⏰ **Task Scheduling**: Built-in support for recurring tasks
- 🔄 **Event-Driven Architecture**: Pub/sub messaging with automatic prefixing and metadata
- 🔒 **Distributed Coordination**: Processing locks, ready states, and service status tracking

## Installation

### Basic Installation

```bash
pip install teenyfactories
```

### With Specific Providers

```bash
# OpenAI only
pip install teenyfactories[openai,redis]

# Anthropic + PostgreSQL
pip install teenyfactories[anthropic,postgres]

# All LLM providers + Redis
pip install teenyfactories[all-llm,redis]

# Everything
pip install teenyfactories[all]
```

## Quick Start

### LLM Usage

```python
import teenyfactories as tf
from pydantic import BaseModel
from langchain_core.prompts import PromptTemplate

# Define response model
class AnalysisResult(BaseModel):
    summary: str
    sentiment: str
    score: float

# Create prompt
template = PromptTemplate.from_template(
    "Analyze the following text: {text}\n{format_instructions}"
)

# Call LLM with automatic validation
result = tf.call_llm(
    template,
    {"text": "This is amazing!"},
    response_model=AnalysisResult
)

print(result.summary)
print(result.sentiment)
print(result.score)
```

### Message Queue Usage

```python
import teenyfactories as tf

# Send a message
tf.send_message('data_ready', {
    'dataset_id': '123',
    'status': 'completed'
})

# Subscribe to messages
def handle_message(message):
    print(f"Received: {message['data']}")

tf.subscribe_to_message(handle_message, topics=['data_ready', 'task_complete'])

# Schedule recurring tasks
def health_check():
    tf.log("Health check running", level='info')

tf.schedule_task(health_check, interval_seconds=60)

# Main event loop
tf.wait_for_next_message_or_scheduled_task()
```

### Distributed Coordination

```python
import teenyfactories as tf

# Mark agent as ready
tf.set_agent_ready('data_profiler')

# Check if another agent is ready
if tf.is_agent_ready('script_executor'):
    print("Script executor is ready!")

# Acquire processing lock
if tf.acquire_processing_lock('data_profiler', event_id):
    try:
        # Process the event
        process_data()
    finally:
        tf.release_processing_lock('data_profiler', event_id)

# Publish service status
tf.publish_service_status('data_profiler', 'running', {'progress': 0.5})
```

## Integrating into Your Factory

This section explains how to integrate TeenyFactories into your own factory projects, whether you're building from scratch or migrating existing code.

### Development Setup (Docker Volume Mount)

For development with hot-reloading, mount the TeenyFactories package from your local clone:

**Prerequisites**:
```bash
# Clone the TeenyFactories core framework
git clone https://github.com/YOUR_ORG/teenyfactories.git
# Note: Can be cloned anywhere on your system
```

**docker-compose.yml**:
```yaml
services:
  your_agent:
    build:
      context: .
      dockerfile: agents/your_agent.dockerfile
    volumes:
      # Mount core package (read-only) - adjust path to your clone location
      - /path/to/teenyfactories/teenyfactories:/app/teenyfactories:ro
      # If cloned side-by-side: ../teenyfactories/teenyfactories:/app/teenyfactories:ro

      # Mount your factory code
      - ./agents:/app/agents
      - ./common:/app/common
    environment:
      - DEFAULT_LLM_PROVIDER=openai
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - MESSAGE_QUEUE_PROVIDER=redis
      - REDIS_HOST=redis
      - FACTORY_PREFIX=your_factory
    depends_on:
      - redis
```

**Dockerfile** (agents/your_agent.dockerfile):
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Copy requirements (if using external dependencies)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Note: teenyfactories mounted as volume, not copied
# Your agent code also mounted as volume

CMD ["python", "-u", "agents/your_agent.py"]
```

**Agent/Worker Code**:
```python
#!/usr/bin/env python
import sys
sys.path.append('/app')  # Ensure /app is in path

import teenyfactories as tf  # Import mounted package

# Your agent logic here
tf.log("Agent starting", level='info')
```

### Production Setup (pip install)

For production deployments, install teenyfactories as a package:

**requirements.txt**:
```
teenyfactories[openai,redis]>=1.0.0
# or for all providers:
# teenyfactories[all]>=1.0.0
```

**Dockerfile**:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy factory code
COPY agents/ ./agents/
COPY common/ ./common/

CMD ["python", "-u", "agents/your_agent.py"]
```

**Agent Code** (same as development):
```python
import teenyfactories as tf
```

### Message Queue Configuration

TeenyFactories supports both Redis and PostgreSQL message queues. Choose based on your infrastructure:

#### Redis (Recommended for Most Cases)

**docker-compose.yml**:
```yaml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  your_agent:
    environment:
      - MESSAGE_QUEUE_PROVIDER=redis
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - REDIS_DB=0
      - FACTORY_PREFIX=your_factory
    depends_on:
      redis:
        condition: service_healthy
```

**Best for**: Fast pub/sub, low latency, simple deployments

#### PostgreSQL (For Database-Heavy Factories)

**docker-compose.yml**:
```yaml
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: teenyfactories
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s

  your_agent:
    environment:
      - MESSAGE_QUEUE_PROVIDER=postgres
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_DB=teenyfactories
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
      - FACTORY_PREFIX=your_factory
    depends_on:
      postgres:
        condition: service_healthy

volumes:
  postgres_data:
```

**Best for**: Factories already using PostgreSQL, transactional guarantees

### LLM Provider Configuration

Configure your preferred LLM provider via environment variables:

**OpenAI** (GPT-4, GPT-4o):
```yaml
environment:
  - DEFAULT_LLM_PROVIDER=openai
  - OPENAI_API_KEY=${OPENAI_API_KEY}
  - OPENAI_MODEL=gpt-4o-mini  # or gpt-4, gpt-4o
```

**Anthropic** (Claude):
```yaml
environment:
  - DEFAULT_LLM_PROVIDER=anthropic
  - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
  - ANTHROPIC_MODEL=claude-3-sonnet-20240229
```

**Google Gemini**:
```yaml
environment:
  - DEFAULT_LLM_PROVIDER=google
  - GOOGLE_API_KEY=${GOOGLE_API_KEY}
  - GOOGLE_MODEL=gemini-pro
```

**Ollama** (Local Models):
```yaml
environment:
  - DEFAULT_LLM_PROVIDER=ollama
  - OLLAMA_MODEL=gpt-oss:20b
  - OLLAMA_BASE_URL=http://ollama:11434  # Or external URL
```

### Example Factory Structure

A typical factory using TeenyFactories follows this structure:

```
your_factory/
├── agents/                        # AI-powered agents
│   ├── agent_analyzer.py          # Data analysis agent
│   ├── agent_planner.py           # Planning agent
│   └── agent_coder.py             # Code generation agent
├── workers/                       # Execution workers
│   ├── worker_input_reader.py     # Input processing
│   ├── worker_executor.py         # Script execution
│   └── worker_output_writer.py    # Output handling
├── common/                        # Shared utilities
│   ├── factory_schemas.py         # Pydantic models
│   └── helpers.py                 # Factory-specific helpers
├── dockervolumes/                 # Persistent data
│   ├── inputs/                    # Input files
│   ├── outputs/                   # Output files
│   └── tracking/                  # State tracking
├── docker-compose.yml             # Service orchestration
├── requirements.txt               # Python dependencies
├── factory.yml                    # Factory metadata
└── README.md                      # Documentation
```

### Example Agent Implementation

**agents/agent_analyzer.py**:
```python
#!/usr/bin/env python
"""
Data Analyzer Agent - Analyzes incoming data and publishes insights
"""
import sys
sys.path.append('/app')

import teenyfactories as tf
from pydantic import BaseModel
from langchain_core.prompts import PromptTemplate

class DataInsight(BaseModel):
    summary: str
    key_findings: list[str]
    confidence_score: float

def analyze_data(message):
    """Handle data analysis requests"""
    data = message.get('data', {})

    tf.log(f"Analyzing data: {data.get('dataset_id')}", level='info')

    # Call LLM with structured output
    prompt = PromptTemplate.from_template(
        "Analyze this dataset: {dataset}\n{format_instructions}"
    )

    result = tf.call_llm(
        prompt,
        {"dataset": data.get('content')},
        response_model=DataInsight
    )

    # Publish insights
    tf.send_message('analysis_complete', {
        'dataset_id': data.get('dataset_id'),
        'insights': result.model_dump()
    })

    tf.log(f"Analysis complete: {result.summary}", level='info')

def main():
    tf.log("Starting data analyzer agent", level='info')

    # Subscribe to events
    tf.subscribe_to_message(
        analyze_data,
        topics=['data_received']
    )

    # Mark as ready
    tf.set_agent_ready('data_analyzer')

    # Main event loop
    tf.wait_for_next_message_or_scheduled_task()

if __name__ == "__main__":
    main()
```

### Example Worker Implementation

**workers/worker_input_reader.py**:
```python
#!/usr/bin/env python
"""
Input Reader Worker - Monitors input directory and publishes events
"""
import sys
sys.path.append('/app')

import teenyfactories as tf
from pathlib import Path

INPUTS_DIR = Path('/app/dockervolumes/inputs')
SCAN_INTERVAL = 30  # seconds

def scan_inputs():
    """Scan input directory for new files"""
    tf.log("Scanning inputs directory...", level='info')

    if not INPUTS_DIR.exists():
        tf.log("Inputs directory not found", level='warn')
        return

    files = list(INPUTS_DIR.glob('*.csv'))

    if files:
        tf.log(f"Found {len(files)} input files", level='info')

        # Publish event for each file
        for file_path in files:
            tf.send_message('data_received', {
                'dataset_id': file_path.stem,
                'file_path': str(file_path),
                'content': file_path.read_text()[:1000]  # Sample
            })

def main():
    tf.log("Starting input reader worker", level='info')

    # Schedule recurring scan
    tf.schedule_task(scan_inputs, interval_seconds=SCAN_INTERVAL)

    # Initial scan
    scan_inputs()

    # Publish status
    tf.send_message('service_status', {
        'service_name': 'input_reader',
        'status': 'running',
        'scan_interval': SCAN_INTERVAL
    })

    # Main event loop
    tf.wait_for_next_message_or_scheduled_task()

if __name__ == "__main__":
    main()
```

### Common Integration Patterns

**Pattern 1: Event-Driven Pipeline**
```python
# Agent 1: Publishes 'step_1_complete'
tf.send_message('step_1_complete', {'data': result})

# Agent 2: Subscribes to 'step_1_complete', publishes 'step_2_complete'
tf.subscribe_to_message(handle_step_1, topics=['step_1_complete'])
tf.send_message('step_2_complete', {'data': result})
```

**Pattern 2: Scheduled Tasks**
```python
# Run every 60 seconds
tf.schedule_task(periodic_health_check, interval_seconds=60)
tf.wait_for_next_message_or_scheduled_task()
```

**Pattern 3: Distributed Coordination**
```python
# Wait for dependencies
if not tf.is_agent_ready('data_profiler'):
    tf.log("Waiting for data profiler...", level='info')
    return

# Acquire lock for processing
if tf.acquire_processing_lock('my_agent', event_id):
    try:
        process_event(event_id)
    finally:
        tf.release_processing_lock('my_agent', event_id)
```

### Migration from Existing Systems

If migrating from existing code:

1. **Replace custom pub/sub** with `tf.send_message()` and `tf.subscribe_to_message()`
2. **Replace LLM calls** with `tf.call_llm()` for automatic validation
3. **Replace logging** with `tf.log()` for consistent format
4. **Add message queue** provider configuration (Redis or PostgreSQL)
5. **Update environment variables** to match TeenyFactories conventions
6. **Test with volume mounts** before production pip install

### Troubleshooting Integration

**Import Error**: `ModuleNotFoundError: No module named 'teenyfactories'`
- Check volume mount path in docker-compose.yml
- Verify `sys.path.append('/app')` in agent code
- Ensure package installed if using pip

**Message Queue Connection Failed**:
- Verify Redis/PostgreSQL service is healthy
- Check `MESSAGE_QUEUE_PROVIDER` environment variable
- Verify connection parameters (host, port, credentials)
- Check `FACTORY_PREFIX` is set and unique

**LLM Provider Error**:
- Verify API key is set correctly
- Check `DEFAULT_LLM_PROVIDER` matches available providers
- Ensure model name is valid for the provider
- Check API key has sufficient credits/permissions

## Configuration

Configure via environment variables:

### LLM Configuration

```bash
# Provider selection
DEFAULT_LLM_PROVIDER=openai  # openai, anthropic, google, ollama, azure_bedrock

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-3-sonnet-20240229

# Google Gemini
GOOGLE_API_KEY=...
GOOGLE_MODEL=gemini-pro

# Ollama
OLLAMA_MODEL=gpt-oss:20b
OLLAMA_BASE_URL=http://localhost:11434

# Azure Bedrock
AZURE_BEDROCK_LLM_URL=https://...
AZURE_BEDROCK_LLM_KEY=...
```

### Message Queue Configuration

```bash
# Provider selection
MESSAGE_QUEUE_PROVIDER=redis  # redis or postgres

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# PostgreSQL
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=teenyfactories
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres

# Factory prefix (for multi-tenancy)
FACTORY_PREFIX=my_factory
```

### Logging Configuration

```bash
DEBUG_LEVEL=INFO  # DEBUG, INFO, WARN, ERROR
```

## Architecture

TeenyFactories uses a provider pattern for both LLM and message queue backends:

### LLM Providers

- **OpenAI**: GPT-4, GPT-4o, GPT-3.5-turbo
- **Anthropic**: Claude 3 models (Opus, Sonnet, Haiku)
- **Google**: Gemini Pro and variants
- **Ollama**: Local models via Ollama server
- **Azure Bedrock**: Azure OpenAI including O3 models

### Message Queue Providers

- **Redis**: Fast pub/sub with key-value storage
- **PostgreSQL**: LISTEN/NOTIFY with database-backed key-value storage

## API Reference

### Logging

- `log(message, level='debug')` - Log message at specified level
- `log_debug(message)` - Log debug message
- `log_info(message)` - Log info message
- `log_warn(message)` - Log warning message
- `log_error(message)` - Log error message

### LLM

- `get_llm_client(provider=None)` - Get LLM client for provider
- `call_llm(prompt, inputs, response_model, provider=None)` - Call LLM with validation
- `clean_json_response(text)` - Clean JSON from LLM response

### Message Queue

- `send_message(topic, payload)` - Send message to topic
- `subscribe_to_message(callback, topics)` - Subscribe to topics
- `schedule_task(func, interval_seconds)` - Schedule recurring task
- `wait_for_next_message_or_scheduled_task()` - Main event loop

### Coordination

- `publish_service_status(name, status, details)` - Publish service status
- `set_agent_ready(name)` - Mark agent as ready
- `is_agent_ready(name)` - Check if agent is ready
- `acquire_processing_lock(agent, event_id, timeout)` - Acquire lock
- `release_processing_lock(agent, event_id)` - Release lock

### Utilities

- `get_aest_now()` - Get current AEST datetime
- `get_timestamp()` - Get ISO format timestamp
- `generate_unique_id()` - Generate UUID
- `AEST_TIMEZONE` - AEST timezone constant

## Development

### Local Development

For developing TeenyFactories core itself, mount the package source:

```yaml
# docker-compose.yml (in your factory repository)
services:
  agent:
    volumes:
      # Path to your teenyfactories clone
      - /path/to/teenyfactories/teenyfactories:/app/teenyfactories:ro
      # If cloned side-by-side with factory: ../teenyfactories/teenyfactories:/app/teenyfactories:ro
```

Then import as usual:

```python
import teenyfactories as tf
```

### Testing

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=teenyfactories
```

## License

MIT License - see LICENSE file for details

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
