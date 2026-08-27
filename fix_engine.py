import pathlib
f = pathlib.Path('blue_team/decision_engine.py')
c = f.read_text(encoding='utf-8')
# Add exception catch for general errors (not just TimeoutError)
old = '    except asyncio.TimeoutError:'
new = '    except (asyncio.TimeoutError, Exception) as _e:\n        if not isinstance(_e, asyncio.TimeoutError):\n            logger.warning("Injection detector error: %s", _e)'
# Just add the except Exception block -- keep existing code
print('Lines around try block:')
lines = c.split('\n')
for i,l in enumerate(lines):
    if 'except asyncio.TimeoutError' in l:
        print(f'Line {i+1}: {l}')
        # Check if there's already an except Exception
        if i+4 < len(lines) and 'except Exception' not in '\n'.join(lines[i:i+5]):
            lines.insert(i+4, '    except Exception as _e:')
            lines.insert(i+5, '        injection_timed_out = True')
            lines.insert(i+6, '        logger.warning(\"Injection detector error: %s, using heuristic\", _e)')
        break
f.write_text('\n'.join(lines), encoding='utf-8')
print('Updated decision_engine.py')
