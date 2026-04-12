#!/usr/bin/env python
"""
TeenyFactories Core Module

Shared utilities for all factories in the teenyfactories ecosystem.
Provides:
- Multi-provider LLM integration (OpenAI, Anthropic, Google, Ollama, Azure Bedrock)
- Redis-based inter-agent communication and event coordination
- Standardized logging
- Common utilities (timestamps, IDs, etc.)

Usage:
    import teenyfactory as tf

    # LLM calls
    response = tf.call_llm(prompt, inputs, response_model=MyModel)

    # Redis events
    tf.publish_event('my_event', {'status': 'completed'})

    # Logging
    tf.log_info("Processing started")
"""

import os
import json
import re
import time
import datetime
from zoneinfo import ZoneInfo
import uuid
from pathlib import Path
import logging
from typing import Optional, Any, Dict, Type, TypeVar

# Redis for inter-container communication
import redis

# Environment variables
from dotenv import load_dotenv
load_dotenv()

# Configuration
PROJECT_NAME = os.getenv('PROJECT_NAME', 'TeenyFactories')
FACTORY_PREFIX = os.getenv('FACTORY_PREFIX', '')

# Logging Configuration
DEBUG_LEVEL = os.getenv('DEBUG_LEVEL', 'INFO').upper()
VALID_LEVELS = {'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARN': logging.WARNING, 'ERROR': logging.ERROR}
LOG_LEVEL = VALID_LEVELS.get(DEBUG_LEVEL, logging.INFO)

# Configure logging
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# =============================================================================
# LOGGING FUNCTIONS
# =============================================================================

def log(message, level='debug'):
    """
    Log message at specified level

    Args:
        message: Message to log
        level: Log level - 'debug', 'info', 'warn', or 'error' (default: 'debug')
    """
    level = level.lower()
    if level == 'debug':
        logger.debug(message)
    elif level == 'info':
        logger.info(message)
    elif level in ('warn', 'warning'):
        logger.warning(message)
    elif level == 'error':
        logger.error(message)
    else:
        logger.debug(f"[{level.upper()}] {message}")  # fallback for unknown levels

def log_debug(message):
    """Log debug message (deprecated: use log(message, level='debug'))"""
    log(message, level='debug')

def log_info(message):
    """Log info message (deprecated: use log(message, level='info'))"""
    log(message, level='info')

def log_warn(message):
    """Log warning message (deprecated: use log(message, level='warn'))"""
    log(message, level='warn')

def log_error(message):
    """Log error message (deprecated: use log(message, level='error'))"""
    log(message, level='error')

# =============================================================================
# TIMEZONE UTILITIES
# =============================================================================

AEST_TIMEZONE = ZoneInfo('Australia/Sydney')

def get_aest_now():
    """Get current datetime in AEST timezone"""
    return datetime.datetime.now(AEST_TIMEZONE)

def get_timestamp():
    """Get current timestamp in ISO format"""
    return get_aest_now().isoformat()

def generate_unique_id():
    """Generate a unique ID for records"""
    return str(uuid.uuid4())

# =============================================================================
# LLM INTEGRATION
# =============================================================================

# Pydantic Models for LLM Response Validation (lazy import)
try:
    from pydantic import BaseModel, ValidationError
    from langchain.output_parsers import PydanticOutputParser
    from langchain_core.prompts import PromptTemplate
    T = TypeVar('T', bound=BaseModel)
except ImportError:
    # Fallback if packages not available
    BaseModel = object
    ValidationError = Exception
    PydanticOutputParser = None
    PromptTemplate = None
    T = TypeVar('T')

def get_llm_client(model_provider=None):
    """Get an LLM client based on the provider"""
    provider = model_provider or os.getenv('DEFAULT_LLM_PROVIDER', 'openai')

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            openai_api_key=os.getenv('OPENAI_API_KEY'),
            model_name=os.getenv('OPENAI_MODEL', 'gpt-4o-mini'),
            temperature=0.3
        )
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            anthropic_api_key=os.getenv('ANTHROPIC_API_KEY'),
            model=os.getenv('ANTHROPIC_MODEL', 'claude-3-sonnet-20240229'),
            temperature=0.3
        )
    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            google_api_key=os.getenv('GOOGLE_API_KEY'),
            model=os.getenv('GOOGLE_MODEL', 'gemini-pro'),
            temperature=0.3
        )
    elif provider == "ollama":
        from langchain_community.chat_models import ChatOllama
        return ChatOllama(
            model=os.getenv('OLLAMA_MODEL', 'gpt-oss:20b'),
            base_url=os.getenv('OLLAMA_BASE_URL', 'http://host.docker.internal:11434'),
            temperature=0.3
        )
    elif provider == "azure_bedrock":
        from langchain_openai import AzureChatOpenAI
        import urllib.parse

        # Parse configuration from AZURE_BEDROCK_LLM_URL
        bedrock_url = os.getenv('AZURE_BEDROCK_LLM_URL')
        bedrock_key = os.getenv('AZURE_BEDROCK_LLM_KEY')

        if not bedrock_url or not bedrock_key:
            raise ValueError("Azure Bedrock requires AZURE_BEDROCK_LLM_URL and AZURE_BEDROCK_LLM_KEY environment variables")

        # Parse the URL to extract components
        # URL format: https://resource.openai.azure.com/openai/deployments/deployment/chat/completions?api-version=version
        parsed = urllib.parse.urlparse(bedrock_url)

        azure_endpoint = f"{parsed.scheme}://{parsed.netloc}/"

        # Extract deployment name from path
        path_parts = parsed.path.split('/')
        if 'deployments' in path_parts:
            deployment_idx = path_parts.index('deployments') + 1
            azure_deployment = path_parts[deployment_idx] if deployment_idx < len(path_parts) else 'o3-mini'
        else:
            azure_deployment = 'o3-mini'

        # Extract API version from query params
        query_params = urllib.parse.parse_qs(parsed.query)
        azure_api_version = query_params.get('api-version', ['2025-01-01-preview'])[0]

        # Create Azure OpenAI client using LangChain
        # Note: o3 models don't support temperature parameter
        client_kwargs = {
            'api_key': bedrock_key,
            'openai_api_version': azure_api_version,
            'azure_endpoint': azure_endpoint,
            'azure_deployment': azure_deployment,
        }

        # Handle temperature parameter based on model type
        if 'o3' in azure_deployment.lower():
            # o3 models don't support temperature - use raw OpenAI client wrapper
            from openai import AzureOpenAI

            from langchain_core.runnables import Runnable
            from langchain_core.messages import HumanMessage, AIMessage

            class O3AzureWrapper(Runnable):
                """Custom wrapper for o3 models that don't support temperature"""
                def __init__(self, azure_client, deployment):
                    self.client = azure_client
                    self.deployment = deployment
                    self.temperature = None  # For debug display

                def invoke(self, input, config=None):
                    # Handle different input types from LangChain chains
                    if hasattr(input, 'to_messages'):
                        # PromptValue input
                        messages = input.to_messages()
                        openai_messages = []
                        for msg in messages:
                            if hasattr(msg, 'content'):
                                role = "user" if isinstance(msg, HumanMessage) else "assistant"
                                openai_messages.append({"role": role, "content": msg.content})
                    elif isinstance(input, str):
                        # Direct string input
                        openai_messages = [{"role": "user", "content": input}]
                    else:
                        # Fallback for other input types
                        openai_messages = [{"role": "user", "content": str(input)}]

                    # Make API call without temperature parameter
                    response = self.client.chat.completions.create(
                        model=self.deployment,
                        messages=openai_messages
                        # Deliberately omitting temperature for o3 models
                    )

                    # Return AIMessage for LangChain compatibility
                    return AIMessage(content=response.choices[0].message.content)

            # Create raw Azure OpenAI client
            raw_client = AzureOpenAI(
                api_key=bedrock_key,
                api_version=azure_api_version,
                azure_endpoint=azure_endpoint
            )

            return O3AzureWrapper(raw_client, azure_deployment)
        else:
            # Other models support temperature
            client_kwargs['temperature'] = 0.3
            return AzureChatOpenAI(**client_kwargs)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")

def _get_model_name(provider: str = None) -> str:
    """Get the model name based on the provider"""
    provider = provider or os.getenv('DEFAULT_LLM_PROVIDER', 'openai')

    if provider == "openai":
        return os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
    elif provider == "anthropic":
        return os.getenv('ANTHROPIC_MODEL', 'claude-3-sonnet-20240229')
    elif provider == "google":
        return os.getenv('GOOGLE_MODEL', 'gemini-pro')
    elif provider == "ollama":
        return os.getenv('OLLAMA_MODEL', 'gpt-oss:20b')
    elif provider == "azure_bedrock":
        # Extract deployment name from AZURE_BEDROCK_LLM_URL for model name
        bedrock_url = os.getenv('AZURE_BEDROCK_LLM_URL', '')
        if bedrock_url:
            import urllib.parse
            parsed = urllib.parse.urlparse(bedrock_url)
            path_parts = parsed.path.split('/')
            if 'deployments' in path_parts:
                deployment_idx = path_parts.index('deployments') + 1
                if deployment_idx < len(path_parts):
                    return path_parts[deployment_idx]
        return 'azure-deployment'
    else:
        return f"unknown-{provider}"

def clean_json_response(response_text: str) -> str:
    """Clean LLM response by extracting JSON content and removing markdown wrappers"""
    # Remove markdown code blocks
    response_text = re.sub(r'^```(?:json)?\s*', '', response_text, flags=re.MULTILINE)
    response_text = re.sub(r'\s*```$', '', response_text, flags=re.MULTILINE)

    # Try to extract JSON from text that may have explanatory content
    # Look for the first occurrence of { and the last occurrence of }
    start_brace = response_text.find('{')
    if start_brace != -1:
        # Find the matching closing brace by counting braces
        brace_count = 0
        for i in range(start_brace, len(response_text)):
            if response_text[i] == '{':
                brace_count += 1
            elif response_text[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    # Found the complete JSON object
                    json_text = response_text[start_brace:i+1]
                    return json_text.strip()

    # If no complete JSON object found, return the cleaned text as-is
    return response_text.strip()

def call_llm(prompt_template, prompt_inputs, response_model: Type[T], model_provider=None, context_info=None, retry_attempt=None) -> T:
    """Call LLM with comprehensive logging and required pydantic parsing"""
    start_time = time.time()
    success = False
    error_message = None
    response_text = ""
    token_info = {}
    parsed_response = None

    try:
        llm = get_llm_client(model_provider)

        # Set up pydantic parsing (now required)
        if PydanticOutputParser is None:
            raise ImportError("PydanticOutputParser not available - install langchain package")
        parser = PydanticOutputParser(pydantic_object=response_model)

        # Add format instructions to the prompt if not already present
        format_instructions = parser.get_format_instructions()
        if hasattr(prompt_template, 'template') and "{format_instructions}" in prompt_template.template:
            prompt_inputs["format_instructions"] = format_instructions
        elif hasattr(prompt_template, 'template'):
            # Append format instructions to the template
            new_template = prompt_template.template + "\n\n{format_instructions}"
            if PromptTemplate is None:
                raise ImportError("PromptTemplate not available - install langchain-core package")
            prompt_template = PromptTemplate.from_template(new_template)
            prompt_inputs["format_instructions"] = format_instructions

        # Create the chain
        chain = prompt_template | llm

        # Make the call
        log_info("💬 Calling LLM")
        result = chain.invoke(prompt_inputs)
        if hasattr(result, 'content'):
            response_text = result.content
        else:
            response_text = str(result)
        # Clean response text (remove markdown wrappers)
        response_text = clean_json_response(response_text)

        # Parse with pydantic (now always required)
        try:
            parsed_response = parser.parse(response_text)
            log_debug(f"✅ Successfully parsed response with {response_model.__name__}")
        except ValidationError as ve:
            log_warn(f"⚠️ Pydantic validation error: {ve}")
            log_info(f"🔍 Raw response text that failed validation: {response_text[:500]}...")
            # Try to extract JSON and parse again
            try:
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    log_info(f"🔍 Extracted JSON for retry: {json_str[:200]}...")
                    parsed_response = response_model.model_validate_json(json_str)
                    log_info(f"✅ Successfully parsed JSON extract with {response_model.__name__}")
                else:
                    log_warn(f"⚠️ No valid JSON found in response: {response_text[:200]}...")
                    raise ValidationError("No valid JSON found in response")
            except Exception as parse_err:
                log_error(f"❌ Failed to parse response: {parse_err}")
                log_info(f"🔍 Final failed response text: {response_text[:300]}...")
                # Raise error since pydantic parsing is now required
                raise Exception(f"Failed to parse LLM response with {response_model.__name__}: {parse_err}")

        # Extract token information if available
        if hasattr(result, 'usage_metadata') and result.usage_metadata is not None:
            usage = result.usage_metadata
            token_info = {
                'input_tokens': usage.get('input_tokens', 0),
                'output_tokens': usage.get('output_tokens', 0),
                'total_tokens': usage.get('total_tokens', 0)
            }

        success = True

    except Exception as e:
        error_message = str(e)
        log_error(f"❌ LLM call failed: {error_message}")

    finally:
        duration_ms = int((time.time() - start_time) * 1000)

        # Log the LLM usage (simplified - no file storage)
        try:
            usage_log = {
                'id': str(uuid.uuid4()),
                'timestamp': get_aest_now().isoformat(),
                'project': PROJECT_NAME,
                'provider': model_provider or os.getenv('DEFAULT_LLM_PROVIDER', 'openai'),
                'model': _get_model_name(model_provider),
                'temperature': 0.3,
                'context': context_info,
                'retry_attempt': retry_attempt,
                'duration_ms': duration_ms,
                'input_tokens': token_info.get('input_tokens'),
                'output_tokens': token_info.get('output_tokens'),
                'total_tokens': token_info.get('total_tokens'),
                'response_model': response_model.__name__ if response_model else None,
                'parsed_successfully': parsed_response is not None,
                'success': success,
                'error': error_message
            }

            # Log to debug
            log_debug(f"📊 LLM Usage: {usage_log.get('provider')}/{usage_log.get('model')} - {duration_ms}ms")

        except Exception as log_err:
            log_warn(f"⚠️ Failed to log LLM usage: {log_err}")

    if success:
        if parsed_response:
            return parsed_response
        else:
            raise Exception("Failed to parse LLM response - pydantic parsing is required")
    else:
        raise Exception(error_message)

# =============================================================================
# REDIS EVENT SYSTEM
# =============================================================================

REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))
REDIS_DB = int(os.getenv('REDIS_DB', '0'))

def format_topic_name(topic: str) -> str:
    """Format topic name with factory prefix if configured"""
    if FACTORY_PREFIX:
        return f"{FACTORY_PREFIX}:{topic}"
    return topic

def get_redis_connection():
    """Get Redis connection"""
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, decode_responses=True)
        r.ping()  # Test connection
        return r
    except Exception as e:
        log_error(f"❌ Failed to connect to Redis: {e}")
        return None

def publish_event(topic: str, data: dict = None):
    """Publish an event to Redis using topic-based channels"""
    try:
        r = get_redis_connection()
        if not r:
            return False

        # Format topic with factory prefix
        formatted_topic = format_topic_name(topic)

        event = {
            'topic': topic,
            'data': data or {},
            'timestamp': get_timestamp(),
            'id': generate_unique_id()
        }

        r.publish(formatted_topic, json.dumps(event))
        log_info(f"📢 Published event to topic: {formatted_topic}")
        return True

    except Exception as e:
        log_error(f"❌ Failed to publish event to topic {topic}: {e}")
        return False

def subscribe_to_events(callback_func, topics: list = None):
    """Subscribe to Redis events with callback function using topic-based channels"""
    try:
        r = get_redis_connection()
        if not r:
            return False

        pubsub = r.pubsub()

        # Subscribe to specific topics with factory prefix
        if topics:
            formatted_topics = [format_topic_name(topic) for topic in topics]
            for formatted_topic in formatted_topics:
                pubsub.subscribe(formatted_topic)
            log_info(f"🎧 Subscribed to topics: {formatted_topics}")
        else:
            # Subscribe to all topics with factory prefix pattern
            pattern = f"{FACTORY_PREFIX}.*" if FACTORY_PREFIX else "*"
            pubsub.psubscribe(pattern)
            log_info(f"🎧 Subscribed to pattern: {pattern}")

        for message in pubsub.listen():
            if message['type'] in ['message', 'pmessage']:
                try:
                    event = json.loads(message['data'])
                    topic = event.get('topic')

                    # Filter by topics if specified
                    if topics is None or topic in topics:
                        callback_func(event)

                except json.JSONDecodeError:
                    log_warn(f"⚠️ Failed to decode event message: {message['data']}")
                except Exception as e:
                    log_error(f"❌ Error processing event: {e}")

    except KeyboardInterrupt:
        log_info("🛑 Event subscription interrupted")
    except Exception as e:
        log_error(f"❌ Failed to subscribe to events: {e}")
        return False

def subscribe_to_events_with_schedule(callback_func, topics: list = None):
    """Subscribe to Redis events with callback function while also running scheduled jobs using topic-based channels"""
    import schedule
    import time

    try:
        r = get_redis_connection()
        if not r:
            return False

        pubsub = r.pubsub()

        # Subscribe to specific topics with factory prefix
        if topics:
            formatted_topics = [format_topic_name(topic) for topic in topics]
            for formatted_topic in formatted_topics:
                pubsub.subscribe(formatted_topic)
            pubsub.get_message(timeout=1)  # Get subscription confirmation
            log_info(f"🎧 Subscribed to topics: {formatted_topics}")
        else:
            # Subscribe to all topics with factory prefix pattern
            pattern = f"{FACTORY_PREFIX}.*" if FACTORY_PREFIX else "*"
            pubsub.psubscribe(pattern)
            pubsub.get_message(timeout=1)  # Get subscription confirmation
            log_info(f"🎧 Subscribed to pattern: {pattern}")

        while True:
            # Check for Redis messages with short timeout
            message = pubsub.get_message(timeout=0.1)
            if message and message['type'] in ['message', 'pmessage']:
                try:
                    event = json.loads(message['data'])
                    topic = event.get('topic')

                    # Filter by topics if specified
                    if topics is None or topic in topics:
                        callback_func(event)

                except json.JSONDecodeError:
                    log_warn(f"⚠️ Failed to decode event message: {message['data']}")
                except Exception as e:
                    log_error(f"❌ Error processing event: {e}")

            # Run pending scheduled jobs
            schedule.run_pending()
            time.sleep(1)  # Small delay to prevent high CPU usage

    except KeyboardInterrupt:
        log_info("🛑 Event subscription interrupted")
    except Exception as e:
        log_error(f"❌ Failed to subscribe to events: {e}")
        return False

def publish_service_status(service_name: str, status: str, details: Dict[str, Any] = None, service_type: str = 'auto'):
    """
    Unified function to publish agent/worker status updates

    Args:
        service_name: Name of the service (e.g., 'data_profiler', 'script_executor')
        status: Status string (e.g., 'running', 'completed', 'failed', 'error')
        details: Additional status details
        service_type: 'agent', 'worker', or 'auto' (auto-detects from service name)
    """
    try:
        # Auto-detect service type if not specified
        if service_type == 'auto':
            if any(agent_type in service_name for agent_type in ['agent', 'profiler', 'planner', 'analyst', 'assistant']):
                service_type = 'agent'
            elif any(worker_type in service_name for worker_type in ['worker', 'executor', 'engine', 'interpreter']):
                service_type = 'worker'
            else:
                service_type = 'service'  # fallback

        # Get container name from environment or derive it
        container_name = os.getenv('HOSTNAME', f"{service_type}_{service_name}")

        event_data = {
            'service_name': service_name,
            'service_type': service_type,
            'container_name': container_name,
            'status': status,
            'timestamp': get_timestamp(),
            **(details or {})
        }

        publish_event('service_status', event_data)
        log_debug(f"⚙️ Published service status: {service_name} ({service_type}) -> {status}")
        return True

    except Exception as e:
        log_error(f"❌ Error publishing service status for {service_name}: {e}")
        return False

def wait_for_event(topic: str, timeout: int = 300):
    """Wait for a specific topic event (blocking)"""
    try:
        r = get_redis_connection()
        if not r:
            return None

        pubsub = r.pubsub()
        formatted_topic = format_topic_name(topic)
        pubsub.subscribe(formatted_topic)
        pubsub.get_message(timeout=1)  # Get subscription confirmation

        log_info(f"⏳ Waiting for event on topic: {formatted_topic} (timeout: {timeout}s)")

        import time
        start_time = time.time()
        while time.time() - start_time < timeout:
            message = pubsub.get_message(timeout=1.0)
            if message and message['type'] == 'message':
                try:
                    event = json.loads(message['data'])
                    if event.get('topic') == topic:
                        log_info(f"✅ Received expected event on topic: {topic}")
                        return event
                except json.JSONDecodeError:
                    continue

        log_warn(f"⏰ Timeout waiting for event on topic: {topic}")
        return None

    except Exception as e:
        log_error(f"❌ Error waiting for event on topic {topic}: {e}")
        return None

def set_agent_ready(agent_name: str):
    """Mark an agent as ready"""
    try:
        r = get_redis_connection()
        if r:
            r.set(f"agent_ready:{agent_name}", "true", ex=3600)  # Expires in 1 hour
            publish_event('agent_ready', {'agent': agent_name})
            log_info(f"✅ Agent marked as ready: {agent_name}")

    except Exception as e:
        log_error(f"❌ Failed to mark agent ready {agent_name}: {e}")

def is_agent_ready(agent_name: str) -> bool:
    """Check if an agent is ready"""
    try:
        r = get_redis_connection()
        if r:
            return bool(r.get(f"agent_ready:{agent_name}"))
        return False
    except Exception as e:
        log_error(f"❌ Failed to check agent ready status {agent_name}: {e}")
        return False

def acquire_processing_lock(agent_name: str, event_id: str, timeout: int = 300) -> bool:
    """Acquire a processing lock for an event to prevent duplicate processing"""
    try:
        r = get_redis_connection()
        if not r:
            return False

        lock_key = f"processing_lock:{agent_name}:{event_id}"
        # Use SET with NX (only if not exists) and EX (expiration)
        result = r.set(lock_key, "locked", nx=True, ex=timeout)

        if result:
            log_info(f"🔒 Acquired processing lock: {agent_name} for event {event_id[:8]}...")
            return True
        else:
            log_info(f"⏭️ Event already being processed: {agent_name} for event {event_id[:8]}...")
            return False

    except Exception as e:
        log_error(f"❌ Failed to acquire processing lock {agent_name}: {e}")
        return False

def release_processing_lock(agent_name: str, event_id: str):
    """Release a processing lock"""
    try:
        r = get_redis_connection()
        if r:
            lock_key = f"processing_lock:{agent_name}:{event_id}"
            r.delete(lock_key)
            log_info(f"🔓 Released processing lock: {agent_name} for event {event_id[:8]}...")
    except Exception as e:
        log_error(f"❌ Failed to release processing lock {agent_name}: {e}")

# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Logging
    'log', 'log_debug', 'log_info', 'log_warn', 'log_error',
    # Timezone
    'get_aest_now', 'get_timestamp', 'AEST_TIMEZONE',
    # Utilities
    'generate_unique_id',
    # LLM
    'get_llm_client', 'call_llm', 'clean_json_response',
    # Redis Events
    'get_redis_connection', 'publish_event', 'subscribe_to_events',
    'subscribe_to_events_with_schedule', 'publish_service_status',
    'wait_for_event', 'set_agent_ready', 'is_agent_ready',
    'acquire_processing_lock', 'release_processing_lock',
    'format_topic_name',
    # Constants
    'PROJECT_NAME', 'FACTORY_PREFIX', 'REDIS_HOST', 'REDIS_PORT', 'REDIS_DB',
]
