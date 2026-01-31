import { describe, test, expect, beforeEach, vi, afterEach } from 'vitest';
import axios from 'axios';
import { PowerMemClient, ApiError } from '../client';
// Mock axios
vi.mock('axios');
const mockedAxios = axios;
/**
 * PowerMemClient 测试套件
 */
describe('PowerMemClient', () => {
    let client;
    let mockAxiosInstance;
    beforeEach(() => {
        // 创建 mock axios 实例
        mockAxiosInstance = {
            get: vi.fn(),
            post: vi.fn(),
            put: vi.fn(),
            delete: vi.fn(),
            interceptors: {
                request: {
                    use: vi.fn(),
                },
                response: {
                    use: vi.fn(),
                },
            },
        };
        mockedAxios.create = vi.fn(() => mockAxiosInstance);
        // 创建客户端实例
        client = new PowerMemClient('http://localhost:8000', 'test-api-key');
    });
    afterEach(() => {
        vi.clearAllMocks();
    });
    /**
     * 测试：构造函数
     */
    describe('constructor', () => {
        test('should create axios instance with correct config', () => {
            expect(mockedAxios.create).toHaveBeenCalledWith({
                baseURL: 'http://localhost:8000/api/v1',
                timeout: 30000,
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer test-api-key',
                },
            });
        });
        test('should remove trailing slash from server URL', () => {
            new PowerMemClient('http://localhost:8000/', 'test-api-key');
            expect(mockedAxios.create).toHaveBeenCalledWith(expect.objectContaining({
                baseURL: 'http://localhost:8000/api/v1',
            }));
        });
    });
    /**
     * 测试：创建记忆
     */
    describe('createMemory', () => {
        test('should create a memory successfully', async () => {
            const mockMemory = {
                content: 'Test memory content',
                user_id: 'user123',
                agent_id: 'agent456',
            };
            const mockResponse = {
                memory_id: 'mem123',
                content: 'Test memory content',
                user_id: 'user123',
                agent_id: 'agent456',
                metadata: {
                    source: 'browser_extension',
                    type: 'web_selection',
                    url: 'https://example.com',
                    title: 'Test Page',
                    domain: 'example.com',
                    timestamp: '2024-01-01T00:00:00Z',
                },
                created_at: '2024-01-01T00:00:00Z',
                updated_at: '2024-01-01T00:00:00Z',
            };
            mockAxiosInstance.post.mockResolvedValue({ data: mockResponse });
            const result = await client.createMemory(mockMemory);
            expect(mockAxiosInstance.post).toHaveBeenCalledWith('/memories', mockMemory);
            expect(result).toEqual(mockResponse);
        });
        test('should handle create memory error', async () => {
            const mockMemory = {
                content: 'Test memory content',
            };
            mockAxiosInstance.post.mockRejectedValue(new Error('Network error'));
            await expect(client.createMemory(mockMemory)).rejects.toThrow('Network error');
        });
    });
    /**
     * 测试：搜索记忆
     */
    describe('searchMemories', () => {
        test('should search memories successfully', async () => {
            const mockResults = [
                {
                    memory_id: 'mem1',
                    content: 'Memory 1',
                    user_id: 'user123',
                    agent_id: 'agent456',
                    metadata: {
                        source: 'browser_extension',
                        type: 'web_selection',
                        url: 'https://example.com',
                        title: 'Test',
                        domain: 'example.com',
                        timestamp: '2024-01-01T00:00:00Z',
                    },
                },
            ];
            mockAxiosInstance.post.mockResolvedValue({
                data: {
                    results: mockResults,
                    total: 1,
                    query: 'test query',
                },
            });
            const result = await client.searchMemories({
                query: 'test query',
                user_id: 'user123',
                agent_id: 'agent456',
                limit: 10,
            });
            expect(mockAxiosInstance.post).toHaveBeenCalledWith('/memories/search', {
                query: 'test query',
                user_id: 'user123',
                agent_id: 'agent456',
                limit: 10,
                filters: undefined,
            });
            expect(result).toEqual(mockResults);
        });
        test('should use default limit if not provided', async () => {
            mockAxiosInstance.post.mockResolvedValue({
                data: { results: [], total: 0, query: 'test' },
            });
            await client.searchMemories({
                query: 'test',
                user_id: 'user123',
            });
            expect(mockAxiosInstance.post).toHaveBeenCalledWith('/memories/search', expect.objectContaining({
                limit: 10,
            }));
        });
        test('should return empty array if results is undefined', async () => {
            mockAxiosInstance.post.mockResolvedValue({
                data: { total: 0, query: 'test' },
            });
            const result = await client.searchMemories({
                query: 'test',
                user_id: 'user123',
            });
            expect(result).toEqual([]);
        });
    });
    /**
     * 测试：获取记忆列表
     */
    describe('getMemories', () => {
        test('should get memories successfully', async () => {
            const mockResponse = {
                memories: [],
                total: 0,
            };
            mockAxiosInstance.get.mockResolvedValue({ data: mockResponse });
            const result = await client.getMemories({
                user_id: 'user123',
                limit: 50,
                offset: 0,
            });
            expect(mockAxiosInstance.get).toHaveBeenCalledWith(expect.stringContaining('user_id=user123'));
            expect(mockAxiosInstance.get).toHaveBeenCalledWith(expect.stringContaining('limit=50'));
            expect(result).toEqual(mockResponse);
        });
        test('should include agent_id in query if provided', async () => {
            mockAxiosInstance.get.mockResolvedValue({
                data: { memories: [], total: 0 },
            });
            await client.getMemories({
                user_id: 'user123',
                agent_id: 'agent456',
            });
            expect(mockAxiosInstance.get).toHaveBeenCalledWith(expect.stringContaining('agent_id=agent456'));
        });
    });
    /**
     * 测试：更新记忆
     */
    describe('updateMemory', () => {
        test('should update memory successfully', async () => {
            const mockUpdates = {
                content: 'Updated content',
            };
            const mockResponse = {
                memory_id: 'mem123',
                content: 'Updated content',
                user_id: 'user123',
                agent_id: 'agent456',
                metadata: {
                    source: 'browser_extension',
                    type: 'web_selection',
                    url: 'https://example.com',
                    title: 'Test',
                    domain: 'example.com',
                    timestamp: '2024-01-01T00:00:00Z',
                },
            };
            mockAxiosInstance.put.mockResolvedValue({ data: mockResponse });
            const result = await client.updateMemory('mem123', mockUpdates);
            expect(mockAxiosInstance.put).toHaveBeenCalledWith('/memories/mem123', mockUpdates);
            expect(result).toEqual(mockResponse);
        });
    });
    /**
     * 测试：删除记忆
     */
    describe('deleteMemory', () => {
        test('should delete memory successfully', async () => {
            mockAxiosInstance.delete.mockResolvedValue({});
            await client.deleteMemory('mem123');
            expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/memories/mem123');
        });
    });
    /**
     * 测试：健康检查
     */
    describe('healthCheck', () => {
        test('should return true when server is healthy', async () => {
            mockAxiosInstance.get.mockResolvedValue({ status: 200 });
            const result = await client.healthCheck();
            expect(mockAxiosInstance.get).toHaveBeenCalledWith('/health');
            expect(result).toBe(true);
        });
        test('should return false when server is unhealthy', async () => {
            mockAxiosInstance.get.mockRejectedValue(new Error('Server error'));
            const result = await client.healthCheck();
            expect(result).toBe(false);
        });
    });
});
/**
 * ApiError 测试套件
 */
describe('ApiError', () => {
    test('should create ApiError with all properties', () => {
        const error = new ApiError('Test error', 400, 'BAD_REQUEST', { field: 'test' });
        expect(error.message).toBe('Test error');
        expect(error.name).toBe('ApiError');
        expect(error.status).toBe(400);
        expect(error.code).toBe('BAD_REQUEST');
        expect(error.details).toEqual({ field: 'test' });
    });
    test('should be instance of Error', () => {
        const error = new ApiError('Test error', 400, 'BAD_REQUEST');
        expect(error).toBeInstanceOf(Error);
        expect(error).toBeInstanceOf(ApiError);
    });
});
