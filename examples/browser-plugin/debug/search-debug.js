/**
 * 浏览器插件搜索调试脚本
 * 
 * 使用方法:
 * 1. 在浏览器插件的 popup 页面打开开发者工具 (F12)
 * 2. 在控制台中运行此脚本
 * 3. 然后在 popup 界面进行搜索
 * 4. 观察控制台输出的调试信息
 * 
 * 或者直接调用 debugSearch("your query") 测试搜索
 */

(function() {
  console.log('%c🔍 PowerMem 搜索调试工具已加载', 'color: #00ff00; font-weight: bold; font-size: 14px');
  
  // 拦截原始的 sendMessage 函数
  if (typeof window.sendMessage !== 'undefined') {
    const originalSendMessage = window.sendMessage;
    
    window.sendMessage = function(...args) {
      const message = args[0];
      
      // 只拦截搜索消息
      if (message && message.type === 'SEARCH_MEMORIES') {
        console.group('%c🔍 搜索请求拦截', 'color: #ff9800; font-weight: bold');
        console.log('搜索查询:', message.payload?.query);
        console.log('完整请求:', JSON.stringify(message, null, 2));
        console.groupEnd();
      }
      
      // 调用原始函数并拦截响应
      const promise = originalSendMessage.apply(this, args);
      
      if (message && message.type === 'SEARCH_MEMORIES') {
        promise.then(response => {
          console.group('%c✅ 搜索响应拦截', 'color: #4caf50; font-weight: bold');
          console.log('成功状态:', response.success);
          console.log('结果数量:', response.data?.results?.length || 0);
          
          if (response.data?.results) {
            console.log('结果总数:', response.data.total);
            console.log('结果详情:');
            
            response.data.results.forEach((result, index) => {
              console.group(`结果 #${index + 1}`);
              console.log('内容:', result.memory?.substring(0, 100) + '...');
              console.log('得分:', result.score);
              console.log('质量分数:', result.metadata?._quality_score);
              console.log('元数据:', result.metadata);
              console.groupEnd();
            });
          }
          
          if (response.error) {
            console.error('错误信息:', response.error);
          }
          
          console.groupEnd();
        }).catch(error => {
          console.group('%c❌ 搜索请求失败', 'color: #f44336; font-weight: bold');
          console.error('错误:', error);
          console.groupEnd();
        });
      }
      
      return promise;
    };
    
    console.log('%c✅ 已拦截 sendMessage 函数', 'color: #2196f3');
  }
  
  // 提供手动测试搜索的函数
  window.debugSearch = async function(query) {
    console.group('%c🧪 手动测试搜索', 'color: #9c27b0; font-weight: bold; font-size: 14px');
    console.log('搜索查询:', query);
    
    try {
      const response = await chrome.runtime.sendMessage({
        type: 'SEARCH_MEMORIES',
        payload: { query }
      });
      
      console.log('%c响应数据:', 'color: #00bcd4; font-weight: bold');
      console.log('成功:', response.success);
      console.log('结果数量:', response.data?.results?.length || 0);
      console.log('完整响应:', response);
      
      // 分析结果
      if (response.success && response.data?.results) {
        const results = response.data.results;
        
        if (results.length === 0) {
          console.log('%c✅ 没有返回结果（符合预期）', 'color: #4caf50; font-weight: bold');
        } else {
          console.log('%c⚠️ 返回了结果，分析如下:', 'color: #ff9800; font-weight: bold');
          
          // 分析分数分布
          const scores = results.map(r => r.score);
          const qualityScores = results.map(r => r.metadata?._quality_score).filter(s => s !== undefined);
          
          console.table({
            '最高分数': Math.max(...scores),
            '最低分数': Math.min(...scores),
            '平均分数': (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(4),
            '最高质量分数': qualityScores.length > 0 ? Math.max(...qualityScores) : 'N/A',
            '最低质量分数': qualityScores.length > 0 ? Math.min(...qualityScores) : 'N/A',
          });
          
          // 显示前3条结果
          console.log('\n前3条结果:');
          results.slice(0, 3).forEach((result, i) => {
            console.log(`\n${i + 1}. ${result.memory?.substring(0, 100)}...`);
            console.log(`   得分: ${result.score}, 质量: ${result.metadata?._quality_score || 'N/A'}`);
          });
        }
      } else if (!response.success) {
        console.error('%c❌ 搜索失败:', 'color: #f44336; font-weight: bold', response.error);
      }
      
    } catch (error) {
      console.error('%c❌ 搜索异常:', 'color: #f44336; font-weight: bold', error);
    }
    
    console.groupEnd();
  };
  
  // 提供查看当前所有记忆的函数
  window.debugGetAllMemories = async function() {
    console.group('%c📚 获取所有记忆', 'color: #673ab7; font-weight: bold; font-size: 14px');
    
    try {
      const response = await chrome.runtime.sendMessage({
        type: 'GET_MEMORIES',
        payload: { limit: 100, offset: 0 }
      });
      
      if (response.success && response.data?.memories) {
        const memories = response.data.memories;
        console.log(`总共 ${memories.length} 条记忆`);
        
        // 显示前5条
        console.log('\n前5条记忆:');
        memories.slice(0, 5).forEach((mem, i) => {
          console.log(`${i + 1}. ${mem.content?.substring(0, 80)}...`);
        });
        
        return memories;
      } else {
        console.error('获取失败:', response.error);
      }
    } catch (error) {
      console.error('异常:', error);
    }
    
    console.groupEnd();
  };
  
  // 网络请求监控（如果可用）
  if (typeof XMLHttpRequest !== 'undefined') {
    const originalOpen = XMLHttpRequest.prototype.open;
    const originalSend = XMLHttpRequest.prototype.send;
    
    XMLHttpRequest.prototype.open = function(method, url, ...args) {
      this._debugUrl = url;
      this._debugMethod = method;
      return originalOpen.apply(this, [method, url, ...args]);
    };
    
    XMLHttpRequest.prototype.send = function(body) {
      if (this._debugUrl && this._debugUrl.includes('/memories/search')) {
        console.group('%c🌐 HTTP搜索请求', 'color: #00bcd4');
        console.log('URL:', this._debugUrl);
        console.log('方法:', this._debugMethod);
        console.log('请求体:', body);
        
        this.addEventListener('load', function() {
          console.log('响应状态:', this.status);
          try {
            const response = JSON.parse(this.responseText);
            console.log('响应数据:', response);
          } catch (e) {
            console.log('响应文本:', this.responseText);
          }
          console.groupEnd();
        });
        
        this.addEventListener('error', function() {
          console.error('请求失败');
          console.groupEnd();
        });
      }
      
      return originalSend.apply(this, arguments);
    };
    
    console.log('%c✅ 已拦截 XMLHttpRequest', 'color: #2196f3');
  }
  
  console.log(`
%c使用方法:
  
1. debugSearch("your query")     - 测试搜索
2. debugGetAllMemories()         - 查看所有记忆
3. 在popup中正常搜索，查看控制台输出

建议测试:
  debugSearch("Nuxt")                                    - 测试真实搜索
  debugSearch("xyz_nonexistent_random_12345")           - 测试不存在的词
  `, 'color: #607d8b');
  
})();
