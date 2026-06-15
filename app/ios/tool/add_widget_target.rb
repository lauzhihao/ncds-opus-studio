# 在 Flutter 的 Runner.xcodeproj 里新增 WidgetKit 扩展 target(灵动岛/Live Activity + 主屏 Widget)。
# 用 xcodeproj gem 脚本化(pbxproj 手改极易错)。可重复运行(先清理旧的同名 target/group)。
#   ruby ios/tool/add_widget_target.rb
require 'xcodeproj'

PROJ = File.expand_path(File.join(__dir__, '..', 'Runner.xcodeproj'))
APP_ID = 'com.claudelight.claudeTrafficLight'
TEAM = 'Z3LULFMC72'
EXT = 'ClaudeWidgetExtension'

project = Xcodeproj::Project.open(PROJ)
runner = project.targets.find { |t| t.name == 'Runner' } or abort 'no Runner target'

# —— 清理旧的(可重跑) ——
project.targets.select { |t| t.name == EXT }.each(&:remove_from_project)
# 移除 Runner 里指向已删 target 的悬空 dependency(否则 add_dependency 会崩在 nil.uuid)
runner.dependencies.dup.each do |dep|
  dep.remove_from_project if dep.target.nil? || (dep.target && dep.target.name == EXT)
end
['ClaudeWidget', 'Shared'].each do |g|
  grp = project.main_group.children.find { |c| c.display_name == g }
  grp.remove_from_project if grp
end
# Runner 里若已加过这两个文件,先移除其 build file(避免重复)
['LiveActivityChannel.swift', 'ClaudeAttributes.swift', 'RelayConfig.swift'].each do |fn|
  runner.source_build_phase.files.dup.each do |bf|
    bf.remove_from_project if bf.file_ref && bf.file_ref.display_name == fn
  end
end
# 旧的 Embed phase
runner.build_phases.select { |p| p.is_a?(Xcodeproj::Project::Object::PBXCopyFilesBuildPhase) && p.name == 'Embed App Extensions' }.each(&:remove_from_project)

# —— 创建扩展 target ——
ext = project.new_target(:app_extension, EXT, :ios, '17.0')
ext.build_configurations.each do |c|
  c.build_settings.merge!(
    'PRODUCT_BUNDLE_IDENTIFIER' => "#{APP_ID}.ClaudeTrafficLightWidget",
    'PRODUCT_NAME' => '$(TARGET_NAME)',
    'INFOPLIST_FILE' => 'ClaudeWidget/Info-Widget.plist',
    'IPHONEOS_DEPLOYMENT_TARGET' => '17.0',
    'SWIFT_VERSION' => '5.0',
    'DEVELOPMENT_TEAM' => TEAM,
    'CODE_SIGN_STYLE' => 'Automatic',
    'GENERATE_INFOPLIST_FILE' => 'YES',
    'SKIP_INSTALL' => 'YES',
    'TARGETED_DEVICE_FAMILY' => '1,2',
    'MARKETING_VERSION' => '1.0',
    'CURRENT_PROJECT_VERSION' => '1',
    'ASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME' => 'AccentColor',
    'LD_RUNPATH_SEARCH_PATHS' => '$(inherited) @executable_path/Frameworks @executable_path/../../Frameworks'
  )
end

# —— 分组与文件引用 ——
wgrp = project.main_group.new_group('ClaudeWidget', 'ClaudeWidget')
sgrp = project.main_group.new_group('Shared', 'Shared')

la   = wgrp.new_reference('ClaudeLiveActivity.swift')
si   = wgrp.new_reference('StatusIconWidget.swift')
wb   = wgrp.new_reference('ClaudeTrafficLightWidgetBundle.swift')
assets = wgrp.new_reference('Assets.xcassets')
fr   = wgrp.new_reference('Fonts/Fraunces-Regular.ttf')
fb   = wgrp.new_reference('Fonts/Fraunces-Bold.ttf')
wgrp.new_reference('Info-Widget.plist') # 仅在导航器可见,经 INFOPLIST_FILE 引用,不入任何 phase

attrs   = sgrp.new_reference('ClaudeAttributes.swift')
relay   = sgrp.new_reference('RelayConfig.swift')

# 扩展:源码 + 资源(AppIntents/Approve-Deny 已移除)
[la, si, wb, attrs, relay].each { |r| ext.source_build_phase.add_file_reference(r) }
[assets, fr, fb].each { |r| ext.resources_build_phase.add_file_reference(r) }

# Runner:channel handler + 共享 ClaudeAttributes(handler 要用)
rgrp = project.main_group.children.find { |c| c.display_name == 'Runner' }
channel = rgrp.new_reference('LiveActivityChannel.swift')
runner.source_build_phase.add_file_reference(channel)
runner.source_build_phase.add_file_reference(attrs)
runner.source_build_phase.add_file_reference(relay) # RelayConfig:把 LA push token 注册到中继
# Runner 加 push 能力 entitlements(aps-environment),否则 pushTokenUpdates 不触发
runner.build_configurations.each { |c| c.build_settings['CODE_SIGN_ENTITLEMENTS'] = 'Runner/Runner.entitlements' }

# —— 依赖 + 嵌入 ——
runner.add_dependency(ext)
embed = runner.new_copy_files_build_phase('Embed App Extensions')
embed.symbol_dst_subfolder_spec = :plug_ins
bf = embed.add_file_reference(ext.product_reference)
bf.settings = { 'ATTRIBUTES' => ['RemoveHeadersOnCopy'] }

# 把 Embed 相位移到 Flutter 的 "Thin Binary" 脚本之前,否则二者 + Info.plist 处理成依赖环。
thin = runner.build_phases.find { |p| p.respond_to?(:display_name) && p.display_name == 'Thin Binary' }
if thin
  runner.build_phases.delete(embed)
  runner.build_phases.insert(runner.build_phases.index(thin), embed)
end

project.save
puts "OK: 已创建 #{EXT};Runner targets=#{project.targets.map(&:name).join(',')}"
