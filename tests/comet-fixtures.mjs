import path from 'node:path';
import {fileURLToPath} from 'node:url';

export const ROOT = fileURLToPath(new URL('../', import.meta.url));
export const SCHEMA2_BASELINE = path.join(ROOT, 'tests/fixtures/comets-schema2');
export const HISTORY_INDEX = path.join(ROOT, 'tests/fixtures/comet-history-index.json');
// Relative overrides are repository-relative, regardless of the runner's cwd.
export const HISTORY_PRODUCT = path.resolve(ROOT, process.env.COMET_HISTORY_CANDIDATE || 'data/comets');
