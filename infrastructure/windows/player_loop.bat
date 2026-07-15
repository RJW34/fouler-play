@echo off
REM Retired mutable-checkout launcher tombstone.
REM Old scheduled tasks may still invoke this path, so it must fail closed.
echo ERROR: infrastructure\windows\player_loop.bat is retired. Use the finite receipt-gated devstream supervisor task.
exit /b 2
