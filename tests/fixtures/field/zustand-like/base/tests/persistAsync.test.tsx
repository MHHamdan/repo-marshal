import { expect, it } from 'vitest'
import { clearStorage } from '../src/middleware/persist'

it('bumps the version', () => {
  expect(clearStorage(1)).toBe(2)
})
