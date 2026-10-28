"""Browser acquisition package.

Provides the Kernel adapter (Playwright Execution mode), the run-wide budget,
artifact validation and the failure taxonomy shared by the acquisition layer.
"""

from app.browser.budget import RunBudget
from app.browser.errors import BrowserFailure, BrowserProviderError
from app.browser.provider import BrowserResult, KernelBrowserProvider, KernelSession

__all__ = [
    "BrowserFailure",
    "BrowserProviderError",
    "BrowserResult",
    "KernelBrowserProvider",
    "KernelSession",
    "RunBudget",
]
