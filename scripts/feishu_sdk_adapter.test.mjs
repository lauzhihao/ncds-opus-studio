import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';

import {
  buildSdkEntryCandidates,
  buildPublicPermissionData,
  normalizePublicPermissionType,
  resolveFeishuApiBase,
  resolveFeishuDocBase,
} from './feishu_sdk_adapter.mjs';

test('resolveFeishuApiBase supports feishu and lark', () => {
  assert.equal(resolveFeishuApiBase('feishu'), 'https://open.feishu.cn');
  assert.equal(resolveFeishuApiBase('lark'), 'https://open.larksuite.com');
});

test('resolveFeishuDocBase supports feishu and lark', () => {
  assert.equal(resolveFeishuDocBase('feishu'), 'https://feishu.cn');
  assert.equal(resolveFeishuDocBase('lark'), 'https://larksuite.com');
});

test('buildPublicPermissionData returns same-tenant readable values for v2 sdk calls', () => {
  assert.deepEqual(buildPublicPermissionData(), {
    external_access_entity: 'closed',
    security_entity: 'anyone_can_view',
    comment_entity: 'anyone_can_view',
    share_entity: 'same_tenant',
    link_share_entity: 'tenant_readable',
  });
});

test('normalizePublicPermissionType rejects unsupported folder public type', () => {
  assert.equal(normalizePublicPermissionType('docx'), 'docx');
  assert.equal(normalizePublicPermissionType('file'), 'file');
  assert.equal(normalizePublicPermissionType('folder'), null);
});

test('buildSdkEntryCandidates prefers the global OpenClaw Feishu plugin sdk path', () => {
  const globalRoot = '/tmp/global-node-modules';
  assert.deepEqual(buildSdkEntryCandidates([globalRoot]), [
    path.join(globalRoot, 'openclaw', 'dist', 'extensions', 'feishu', 'node_modules', '@larksuiteoapi', 'node-sdk', 'lib', 'index.js'),
    path.join(globalRoot, 'openclaw', 'dist', 'extensions', 'feishu', 'node_modules', '@larksuiteoapi', 'node-sdk', 'es', 'index.js'),
    path.join(globalRoot, '@openclaw', 'feishu', 'node_modules', '@larksuiteoapi', 'node-sdk', 'lib', 'index.js'),
    path.join(globalRoot, '@openclaw', 'feishu', 'node_modules', '@larksuiteoapi', 'node-sdk', 'es', 'index.js'),
    path.join(globalRoot, 'openclaw', 'node_modules', '@larksuiteoapi', 'node-sdk', 'lib', 'index.js'),
    path.join(globalRoot, 'openclaw', 'node_modules', '@larksuiteoapi', 'node-sdk', 'es', 'index.js'),
  ]);
});
