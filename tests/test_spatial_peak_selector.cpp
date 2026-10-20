#include "spatial_peak_selector.hpp"
#include <cassert>
#include <iostream>
#include <vector>
struct Peak { int x,y; float strength; };
int main() {
  std::vector<Peak> points;
  // Bright concentrated artifact competes with a much fainter real moving source.
  for (int i=0;i<2000;++i)
    points.push_back({300+i%32,300+(i/32)%32,100.0f-i*0.01f});
  points.push_back({650,700,6.0f});
  auto traditional=points;
  std::sort(traditional.begin(),traditional.end(),
    [](const Peak&a,const Peak&b) {return a.strength>b.strength;});
  traditional.resize(700);
  bool missed=true;
  for (auto p:traditional) if(p.x==650 && p.y==700)missed=false;
  assert(missed);
  auto balanced=comet::selectSpatiallyBalanced(points,1024,1024,700);
  assert(balanced.size()==700);
  bool found=false;
  for (auto p:balanced)if(p.x==650&&p.y==700)found=true;
  assert(found);
  // No changes to complete lists; no fabricated peaks.
  auto shortList=comet::selectSpatiallyBalanced(
      std::vector<Peak>{{100,100,2.0f},{120,100,5.0f}},1024,1024,700);
  assert(shortList.size()==2&&shortList[0].strength==5.0f);
  std::cout<<"PASS: balanced selection preserves faint geographically separated peak without exceeding cap\n";
}
