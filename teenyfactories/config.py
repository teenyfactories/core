"""Configuration and environment variable management for teenyfactories"""

import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# =============================================================================
# PROJECT CONFIGURATION
# =============================================================================

PROJECT_NAME = os.getenv('PROJECT_NAME', 'TeenyFactories')
FACTORY_PREFIX = os.getenv('FACTORY_PREFIX', '')

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================

DEBUG_LEVEL = os.getenv('DEBUG_LEVEL', 'INFO').upper()
VALID_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARN': logging.WARNING,
    'ERROR': logging.ERROR
}
LOG_LEVEL = VALID_LEVELS.get(DEBUG_LEVEL, logging.INFO)

# Configure logging
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# =============================================================================
# POSTGRESQL CONFIGURATION (Message Queue + Job Queue)
# =============================================================================

POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'postgres')
POSTGRES_PORT = int(os.getenv('POSTGRES_PORT', '5432'))
POSTGRES_DB = os.getenv('POSTGRES_DB', 'teenyfactories')
POSTGRES_USER = os.getenv('POSTGRES_USER', 'postgres')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', 'postgres')

# =============================================================================
# LLM PROVIDER CONFIGURATION
# =============================================================================

DEFAULT_LLM_PROVIDER = os.getenv('DEFAULT_LLM_PROVIDER', 'openai')

# Provider-specific environment variables are read directly by provider classes
# OpenAI: OPENAI_API_KEY, OPENAI_MODEL
# Anthropic: ANTHROPIC_API_KEY, ANTHROPIC_MODEL
# Google: GOOGLE_API_KEY, GOOGLE_MODEL
# Ollama: OLLAMA_MODEL, OLLAMA_BASE_URL
# Azure Bedrock: AZURE_BEDROCK_LLM_URL, AZURE_BEDROCK_LLM_KEY
