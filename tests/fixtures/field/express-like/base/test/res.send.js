'use strict';

var assert = require('node:assert');
var res = require('../lib/response');

describe('res.send()', function () {
  it('returns the body', function () {
    assert.strictEqual(res.send('x'), 'x');
  });
});
