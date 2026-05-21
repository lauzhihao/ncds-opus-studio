import test from 'node:test';
import assert from 'node:assert/strict';

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

test('buildSdkEntryCandidates returns empty after lark-cli migration', () => {
  // 历史上这里测试 @larksuiteoapi SDK 加载路径；改造为 lark-cli 后 SDK 不再加载，
  // 函数只是为了兼容保留，固定返回空数组。
  assert.deepEqual(buildSdkEntryCandidates(['/tmp/anything']), []);
  assert.deepEqual(buildSdkEntryCandidates(), []);
});
