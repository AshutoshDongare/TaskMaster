"""
Central Tool Registry — maps tool names to callable implementations.

The Orchestrator and specialist agents reference tools by name. The registry
resolves names to actual callables with metadata (risk level, required params).
"""

from typing import Callable, Dict, Any, Optional, List
from dataclasses import dataclass, field
import logging

from core.task_manifest import RiskLevel

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    """Metadata and callable for a registered tool."""
    name: str
    description: str
    callable: Callable
    risk_level: RiskLevel = RiskLevel.LOW
    required_params: List[str] = field(default_factory=list)
    optional_params: List[str] = field(default_factory=list)
    specialist_team: Optional[str] = None  # Which specialist team owns this tool


class ToolRegistry:
    """
    Central registry mapping tool names to callables.

    Usage:
        registry = ToolRegistry()

        @registry.register("navigate", risk_level=RiskLevel.LOW,
                           description="Navigate browser to a URL",
                           required_params=["url"])
        async def navigate(url: str, **kwargs):
            ...

        # Later, execute by name:
        result = await registry.execute("navigate", url="https://example.com")
    """

    def __init__(self):
        self._tools: Dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        description: str = "",
        risk_level: RiskLevel = RiskLevel.LOW,
        required_params: Optional[List[str]] = None,
        optional_params: Optional[List[str]] = None,
        specialist_team: Optional[str] = None,
    ) -> Callable:
        """Decorator to register a tool function."""
        def decorator(func: Callable) -> Callable:
            spec = ToolSpec(
                name=name,
                description=description or func.__doc__ or "",
                callable=func,
                risk_level=risk_level,
                required_params=required_params or [],
                optional_params=optional_params or [],
                specialist_team=specialist_team,
            )
            self._tools[name] = spec
            logger.info(f"Registered tool: {name} (risk={risk_level.value})")
            return func
        return decorator

    def get(self, name: str) -> Optional[ToolSpec]:
        """Get a tool spec by name."""
        return self._tools.get(name)

    async def execute(self, name: str, **params) -> Dict[str, Any]:
        """
        Execute a registered tool by name.

        Args:
            name: The registered tool name
            **params: Parameters to pass to the tool

        Returns:
            Dict with at minimum {"success": bool, "result": Any}

        Raises:
            ValueError: If tool not found or missing required params
        """
        spec = self._tools.get(name)
        if not spec:
            raise ValueError(f"Tool '{name}' not found in registry. "
                             f"Available: {list(self._tools.keys())}")

        # Validate required params
        missing = [p for p in spec.required_params if p not in params]
        if missing:
            raise ValueError(f"Tool '{name}' missing required params: {missing}")

        try:
            result = await spec.callable(**params)
            return {
                "success": True,
                "result": result,
                "tool": name,
            }
        except Exception as e:
            logger.error(f"Tool '{name}' execution failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "tool": name,
            }

    def list_tools(self, specialist_team: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all registered tools, optionally filtered by specialist team."""
        tools = self._tools.values()
        if specialist_team:
            tools = [t for t in tools if t.specialist_team == specialist_team]

        return [
            {
                "name": t.name,
                "description": t.description,
                "risk_level": t.risk_level.value,
                "required_params": t.required_params,
                "specialist_team": t.specialist_team,
            }
            for t in tools
        ]

    def get_risk_level(self, name: str) -> RiskLevel:
        """Get the risk level for a tool."""
        spec = self._tools.get(name)
        if not spec:
            # Unknown tools default to HIGH risk (safe default)
            return RiskLevel.HIGH
        return spec.risk_level


# Global singleton registry
tool_registry = ToolRegistry()
