## Welcome to My Blog

## NJU 操作系统

[NJU 操作系统：设计与实现 (2023 春季学期)](https://jyywiki.cn/OS/2023/)

## Metal 

[Metal-Tutorial](https://github.com/We-are-intense/Metal-Tutorial)

MTKView 是MetalKit的一个类，layer 是 CAMetalLayer，负责渲染内容到屏幕。

驱动模式
提供三种渲染模式。分别由两个变量控制。

- 默认模式，paused 和 enableSetNeedsDisplay 都是NO，渲染由内部的定时器驱动
- paused 和 enableSetNeedsDisplay 都是YES，由view的渲染通知驱动，比如调用setNeedsDisplay
- paused 是 YES， enableSetNeedsDisplay 是 NO， 这个由主动调用MTKView 的draw方法

渲染方法

- 子类MTKView，在drawRect:方法里实现
- 设置MTKView的代理，在代理drawInMTKView:方法实现

## 算法

[fucking-algorithm](https://github.com/labuladong/fucking-algorithm)

## 定点数
[定点数优化：性能成倍提升](https://zhuanlan.zhihu.com/p/149517485)
## 其它

[浙江大学课程攻略共享计划](https://github.com/QSCTech/zju-icicles)