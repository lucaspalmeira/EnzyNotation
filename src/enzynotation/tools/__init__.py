"""External evidence-provider command wrappers."""

from enzynotation.tools.base import ExternalTool, ToolConfigurationError
from enzynotation.tools.blast import BlastConfig, BlastTool, load_blast_config
from enzynotation.tools.clean import CleanConfig, CleanTool, load_clean_config
from enzynotation.tools.foldseek import (
    FoldseekConfig,
    FoldseekTool,
    load_foldseek_config,
)
from enzynotation.tools.hmmer import HmmerConfig, HmmerTool, load_hmmer_config
from enzynotation.tools.interpro import (
    InterProConfig,
    InterProTool,
    load_interpro_config,
)
from enzynotation.tools.tmalign import TMAlignConfig, TMAlignTool, load_tmalign_config

__all__ = [
    "BlastConfig",
    "BlastTool",
    "CleanConfig",
    "CleanTool",
    "ExternalTool",
    "FoldseekConfig",
    "FoldseekTool",
    "HmmerConfig",
    "HmmerTool",
    "InterProConfig",
    "InterProTool",
    "ToolConfigurationError",
    "TMAlignConfig",
    "TMAlignTool",
    "load_blast_config",
    "load_clean_config",
    "load_foldseek_config",
    "load_hmmer_config",
    "load_interpro_config",
    "load_tmalign_config",
]
